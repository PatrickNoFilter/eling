"""Privacy & safety pipeline for Eling memory ingestion.

Implements:
- SHA-256 dedup (5-minute rolling window)
- PII/secret pattern stripping (GitHub tokens, API keys, AWS, PEM, etc.)
- Redaction logging

Pipeline: input → SHA-256 dedup → strip_secrets → clean output + redaction log
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from typing import Pattern


# ---------------------------------------------------------------------------
# Secret patterns — comprehensive set
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "github_token",
        re.compile(
            r"ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|ghu_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36}|ghr_[a-zA-Z0-9]{36}"
        ),
    ),
    (
        "github_old_token",
        re.compile(r"ghp_[A-Za-z0-9]{36,40}"),
    ),
    (
        "openai_api_key",
        re.compile(r"sk-[a-zA-Z0-9]{40,50}"),
    ),
    (
        "anthropic_api_key",
        re.compile(r"sk-ant-[a-zA-Z0-9]{40,60}"),
    ),
    (
        "bearer_token",
        re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.\+/]{20,}"),
    ),
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ),
    (
        "aws_secret_key",
        re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key[\s\"'=:]+\S{40}"),
    ),
    (
        "google_api_key",
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    ),
    (
        "slack_token",
        re.compile(r"xox[baprs]-[0-9a-zA-Z\-]{10,80}"),
    ),
    (
        "discord_token",
        re.compile(r"[MN][A-Za-z\d]{23,25}\.[A-Za-z\d]{6}\.[A-Za-z\d\-_]{27,38}"),
    ),
    (
        "jwt_token",
        re.compile(r"eyJ[a-zA-Z0-9\-_]+\.eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+"),
    ),
    (
        "private_key_pem",
        re.compile(
            r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
    (
        "pgp_private_key",
        re.compile(
            r"-----BEGIN PGP PRIVATE KEY BLOCK-----[\s\S]*?-----END PGP PRIVATE KEY BLOCK-----"
        ),
    ),
    (
        "generic_long_secret",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|apikey|api_key|auth)[\s\"'=:]+\S{20,}"
        ),
    ),
    (
        "connection_string",
        re.compile(
            r"(?i)(?:mysql|postgres|mongodb|redis|amqp|rabbitmq)://[^\s]+@[^\s]+"
        ),
    ),
    (
        "heroku_api_key",
        re.compile(r"heroku[a-zA-Z0-9\-_]{20,}"),
    ),
    (
        "npm_token",
        re.compile(r"npm_[a-zA-Z0-9]{36,48}"),
    ),
    (
        "git_credentials_in_url",
        re.compile(r"https://[^:@\s]+:[^@\s]+@[^\s]+"),
    ),
    (
        "generic_base64_secret",
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    ),
]

# Patterns that should produce a warning but not full redaction (e.g., env file lines)
SENSITIVE_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "env_var_assignment",
        re.compile(r"^export\s+\w+=(?:\"[^\"]*\"|'[^']*'|\S+)$", re.MULTILINE),
    ),
]


def strip_secrets(text: str) -> tuple[str, list[str]]:
    """Strip known secret patterns from text.

    Returns:
        (sanitized_text, redacted_kinds) — cleaned text and list of what was redacted.
    """
    sanitized = text
    redacted: set[str] = set()

    for name, pattern in SECRET_PATTERNS:
        if pattern.search(sanitized):
            sanitized = pattern.sub(f"[REDACTED:{name}]", sanitized)
            redacted.add(name)

    # Also warn about sensitive-but-not-destructive patterns
    for name, pattern in SENSITIVE_PATTERNS:
        if pattern.search(sanitized):
            redacted.add(f"sensitive:{name}")

    return sanitized, sorted(redacted)


def redact_kinds(kinds: list[str]) -> str:
    """Pretty-print redacted kinds for logging."""
    if not kinds:
        return ""
    return ", ".join(f"[{k}]" for k in kinds)


# ---------------------------------------------------------------------------
# SHA-256 dedup — 5-minute rolling window
# ---------------------------------------------------------------------------


class DedupCache:
    """Time-aware dedup with configurable TTL.

    Stores SHA-256 hashes of content. Expired entries are pruned on each check.
    """

    def __init__(self, ttl: float = 300.0, max_size: int = 10000):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: OrderedDict[str, float] = OrderedDict()

    def is_duplicate(self, content: str) -> bool:
        """Check if content (or its hash) was seen within TTL window.

        Returns True if duplicate (caller should skip storage).
        """
        now = time.time()
        h = self._hash(content)

        # Prune expired
        while self._cache and next(iter(self._cache.values())) < now - self.ttl:
            self._cache.popitem(last=False)

        if h in self._cache:
            return True

        self._cache[h] = now
        # Evict oldest if over max
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

        return False

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self):
        self._cache.clear()


# ---------------------------------------------------------------------------
# Composite pipeline
# ---------------------------------------------------------------------------


class PrivacyPipeline:
    """Full privacy pipeline: dedup → strip → report."""

    def __init__(self, dedup_ttl: float = 300.0):
        self.dedup = DedupCache(ttl=dedup_ttl)

    def process(self, content: str, *, skip_dedup: bool = False) -> dict:
        """Run full pipeline. Returns:
        - clean: sanitized content (with secrets redacted)
        - is_duplicate: bool
        - redacted: list of redacted kinds
        """
        # 1. Strip secrets first (before hashing — hash the clean version for safety)
        clean, redacted = strip_secrets(content)

        # 2. Dedup on clean content
        dup = False
        if not skip_dedup:
            dup = self.dedup.is_duplicate(clean)

        return {
            "clean": clean,
            "is_duplicate": dup,
            "redacted": redacted,
        }

    def stats(self) -> dict:
        return {"dedup_cache_size": self.dedup.size}


# Module-level default pipeline
_default_pipeline: PrivacyPipeline | None = None


def get_pipeline() -> PrivacyPipeline:
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = PrivacyPipeline()
    return _default_pipeline


def process(content: str, *, skip_dedup: bool = False) -> dict:
    """Quick-access: run default pipeline on content."""
    return get_pipeline().process(content, skip_dedup=skip_dedup)

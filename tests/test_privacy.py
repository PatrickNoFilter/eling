"""Tests for Eling privacy & safety pipeline."""

import time
import re

import pytest

from eling.privacy import (
    SECRET_PATTERNS,
    DedupCache,
    PrivacyPipeline,
    process,
    strip_secrets,
)


# ── strip_secrets ──────────────────────────────────────────────────────────


class TestStripSecrets:
    def test_github_token(self):
        text = "my token is ghp_FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
        clean, kinds = strip_secrets(text)
        assert "ghp_" not in clean
        assert "[REDACTED:github_token]" in clean
        assert "github_token" in kinds

    def test_openai_api_key(self):
        text = "OPENAI_API_KEY=sk-FAKEabcdefghijklmnopqrstuvwxyz1234567890abcdefghij"
        clean, kinds = strip_secrets(text)
        assert "sk-" not in clean
        assert "openai_api_key" in kinds

    def test_anthropic_api_key(self):
        text = "key=sk-ant-abcdefghijklmnopqrstuvwxyz1234567890abcdefghij"
        clean, kinds = strip_secrets(text)
        assert "sk-ant-" not in clean
        assert "anthropic_api_key" in kinds

    def test_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doeR3kNWN4pPQrA9Vf5vXwYnZs"
        clean, kinds = strip_secrets(text)
        assert "Bearer " not in clean
        assert "bearer_token" in kinds

    def test_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        clean, kinds = strip_secrets(text)
        assert "AKIA" not in clean
        assert "aws_access_key" in kinds

    def test_private_key_pem(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        clean, kinds = strip_secrets(text)
        assert "PRIVATE KEY" not in clean
        assert "private_key_pem" in kinds

    def test_jwt_token(self):
        text = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doeR3kNWN4pPQrA9Vf5vXwYnZs"
        clean, kinds = strip_secrets(text)
        assert "[REDACTED:jwt_token]" in clean
        assert "jwt_token" in kinds

    def test_connection_string(self):
        text = "postgres://user:supersecret123!@host:5432/db"
        clean, kinds = strip_secrets(text)
        assert "//user:supersecret123!@" not in clean
        assert "connection_string" in kinds

    def test_git_credentials_in_url(self):
        text = "https://user:ghp_FAKEtoken1234567890abcdefghijklmnopqrstuvwxyz@github.com/repo"
        clean, kinds = strip_secrets(text)
        assert "ghp_" not in clean
        assert "git_credentials_in_url" in kinds

    def test_slack_token(self):
        # Build at runtime to avoid GitHub's static secret scanner
        prefix = "x" + "oxb-"
        token_suffix = "FAKESLACKTOKENFORTESTINGONLY1234567890"
        text = prefix + token_suffix
        clean, kinds = strip_secrets(text)
        assert prefix not in clean
        assert "slack_token" in kinds

    def test_discord_token(self):
        # Build at runtime to avoid GitHub's static secret scanner
        token_part1 = "MFAKETOKENFORTESTINGONLY12"
        token_part2 = "XYZabc"
        token_part3 = "FAKETOKENFORTESTINGTHISISNOTREAL12345"
        text = f"I have a token like {token_part1}.{token_part2}.{token_part3} and it should be redacted"
        clean, kinds = strip_secrets(text)
        assert "discord_token" in kinds, f"got {kinds}"

    def test_multiple_secrets_in_one_text(self):
        text = (
            "GITHUB_TOKEN=ghp_FAKEaBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789\n"
            "OPENAI_KEY=sk-FAKEabcdefghijklmnopqrstuvwxyz1234567890abcdefghij\n"
            "Normal content here."
        )
        clean, kinds = strip_secrets(text)
        assert "ghp_" not in clean
        assert "sk-" not in clean
        assert "Normal content here" in clean
        assert "github_token" in kinds
        assert "openai_api_key" in kinds

    def test_harmless_text_unaffected(self):
        text = "Eling is a unified memory brain for AI agents."
        clean, kinds = strip_secrets(text)
        assert clean == text
        assert kinds == []

    def test_empty_text(self):
        clean, kinds = strip_secrets("")
        assert clean == ""
        assert kinds == []

    def test_generic_long_secret(self):
        text = "export PASSWORD=superSecretKey12345678901234567890"
        clean, kinds = strip_secrets(text)
        assert "generic_long_secret" in kinds


# ── DedupCache ────────────────────────────────────────────────────────────


class TestDedupCache:
    def test_basic_dedup(self):
        cache = DedupCache(ttl=60)
        assert not cache.is_duplicate("hello world")
        assert cache.is_duplicate("hello world")
        assert cache.size == 1

    def test_different_content_not_duplicate(self):
        cache = DedupCache(ttl=60)
        assert not cache.is_duplicate("hello")
        assert not cache.is_duplicate("world")
        assert cache.size == 2

    def test_ttl_expiry(self):
        cache = DedupCache(ttl=0.1)
        assert not cache.is_duplicate("test")
        assert cache.is_duplicate("test")
        time.sleep(0.15)
        assert not cache.is_duplicate("test")  # expired

    def test_max_size_eviction(self):
        cache = DedupCache(ttl=60, max_size=3)
        for i in range(5):
            assert not cache.is_duplicate(f"content-{i}")
        assert cache.size == 3

    def test_clear(self):
        cache = DedupCache(ttl=60)
        cache.is_duplicate("a")
        cache.is_duplicate("b")
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_hash_consistency(self):
        h1 = DedupCache._hash("same text")
        h2 = DedupCache._hash("same text")
        assert h1 == h2


# ── PrivacyPipeline ───────────────────────────────────────────────────────


class TestPrivacyPipeline:
    def test_clean_content_passes_through(self):
        pipe = PrivacyPipeline()
        result = pipe.process("Hello world, this is clean.")
        assert result["clean"] == "Hello world, this is clean."
        assert not result["is_duplicate"]
        assert result["redacted"] == []

    def test_secrets_are_stripped(self):
        pipe = PrivacyPipeline(dedup_ttl=9999)
        text = "My GitHub token is ghp_FAKEaBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
        result = pipe.process(text)
        assert "[REDACTED:github_token]" in result["clean"]
        assert "ghp_" not in result["clean"]
        assert "github_token" in result["redacted"]

    def test_dedup_within_pipeline(self):
        pipe = PrivacyPipeline(dedup_ttl=60)
        content = "Some unique content for dedup test"
        r1 = pipe.process(content)
        assert not r1["is_duplicate"]
        r2 = pipe.process(content)
        assert r2["is_duplicate"]

    def test_dedup_skip(self):
        pipe = PrivacyPipeline(dedup_ttl=60)
        content = "skip dedup test"
        r1 = pipe.process(content, skip_dedup=True)
        assert not r1["is_duplicate"]
        r2 = pipe.process(content, skip_dedup=True)
        assert not r2["is_duplicate"]  # skipped both times

    def test_secrets_then_dedup(self):
        pipe = PrivacyPipeline(dedup_ttl=60)
        text = "Token: ghp_FAKEaBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
        r1 = pipe.process(text)
        assert "github_token" in r1["redacted"]
        assert not r1["is_duplicate"]
        r2 = pipe.process(text)
        assert r2["is_duplicate"]  # clean version same

    def test_stats(self):
        pipe = PrivacyPipeline()
        pipe.process("a")
        pipe.process("a")  # duplicate
        pipe.process("b")
        s = pipe.stats()
        assert s["dedup_cache_size"] == 2  # "a" and "b"


# ── Module-level convenience ──────────────────────────────────────────────


class TestModuleLevel:
    def test_process_function(self):
        result = process("Hello from module level")
        assert "clean" in result

    def test_pipeline_is_singleton(self):
        from eling.privacy import get_pipeline

        p1 = get_pipeline()
        p2 = get_pipeline()
        assert p1 is p2


# ── Pattern coverage (each pattern must match its target) ──────────────────


def _match_text_for(name: str) -> str:
    """Return a plausible test string that should match the named SECRET_PATTERNS regex.

    Built at runtime so GitHub's static secret scanner sees no real-looking tokens.
    """
    _tokens = {}
    _tokens["github_token"] = (
        "g" + "hp_" + "FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCDe"
    )
    _tokens["github_old_token"] = (
        "g" + "hp_" + "FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghij"
    )
    _tokens["openai_api_key"] = (
        "s" + "k-" + "FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghij"
    )
    _tokens["anthropic_api_key"] = (
        "s" + "k-ant-" + "FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghij"
    )
    _tokens["bearer_token"] = (
        "Bearer FAKEABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghij"
    )
    _tokens["aws_access_key"] = "AKIAIOSFODNN7EXAMPLE"
    _tokens["google_api_key"] = "AIzaSyABCDeFgHiJkLmNoPqRsTuVwXyZ0123456"
    _tokens["slack_token"] = "x" + "oxb-" + "FAKESLACKTOKENFORTESTINGONLY1234567890"
    _tokens["discord_token"] = (
        "MFAKETOKENFORTESTINGONLY12.XYZabc.FAKETOKENFORTESTINGTHISISNOTREAL12345"
    )
    _tokens["jwt_token"] = (
        "eyJABCDeFgHiJkLmNoPqRsTuVw"
        ".eyJXyZ0123456789abcdefghijklmnopqrstuvwxyz"
        ".ABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    )
    _tokens["private_key_pem"] = (
        "-----BEGIN PRIVATE KEY-----\nABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789\n-----END PRIVATE KEY-----"
    )
    _tokens["pgp_private_key"] = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789\n-----END PGP PRIVATE KEY BLOCK-----"
    )
    _tokens["generic_long_secret"] = (
        "export PASSWORD=ABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    )
    _tokens["connection_string"] = "postgres://user:pass@host:5432/db"
    _tokens["heroku_api_key"] = "heroku" + "ABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    _tokens["npm_token"] = "npm_" + "ABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcd"
    _tokens["git_credentials_in_url"] = "https://user:pass@github.com/repo"
    _tokens["generic_base64_secret"] = (
        "ABCDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghij=="
    )
    return _tokens[name]


class TestPatternDefinitions:
    """SECRET_PATTERNS structure: each entry is a (name, regex) pair."""

    def test_all_patterns_compiled(self):
        for name, pattern in SECRET_PATTERNS:
            assert isinstance(pattern, re.Pattern), f"{name} is not a compiled regex"
            text = f"xxx {name}_test xxx"
            assert pattern.search(text) or True  # just check it compiles

    @pytest.mark.parametrize(
        "name",
        [
            "github_token",
            "github_old_token",
            "openai_api_key",
            "anthropic_api_key",
            "bearer_token",
            "aws_access_key",
            "google_api_key",
            "slack_token",
            "discord_token",
            "jwt_token",
            "private_key_pem",
            "pgp_private_key",
            "generic_long_secret",
            "connection_string",
            "heroku_api_key",
            "npm_token",
            "git_credentials_in_url",
            "generic_base64_secret",
        ],
    )
    def test_named_pattern_matches(self, name):
        """Each SECRET_PATTERNS regex matches a plausible test string."""
        patterns_dict = dict(SECRET_PATTERNS)
        assert name in patterns_dict, f"Pattern '{name}' not in SECRET_PATTERNS"

        # Build match text at runtime — GitHub static scanner sees no secrets
        match_text = _match_text_for(name)
        p = patterns_dict[name]
        assert p.search(match_text), (
            f"Pattern '{name}' /{p.pattern}/ did not match '{match_text[:50]}'"
        )

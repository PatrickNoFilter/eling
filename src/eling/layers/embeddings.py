"""Optional vector embedding layer for semantic search.

Supports two embedding providers:
1. sentence-transformers (local, when pip install eling[embeddings])
2. API-based embeddings via Mistral API (fallback, no heavy deps needed)

Inspired by Memvid's vec + api_embed design — model-agnostic,
with model binding to prevent index corruption.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_np = None


def _get_np():
    """Lazy import numpy — avoids import failures on systems without numpy (e.g. bare Termux)."""
    global _np
    if _np is None:
        try:
            import numpy as __np

            _np = __np
        except ImportError:
            _np = False  # sentinel: don't retry
    return _np if _np is not False else None


_HAS_SENTENCE_TRANSFORMERS = False
_MODEL = None

try:
    from sentence_transformers import SentenceTransformer

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore


# ── API Embedding Provider (Mistral) ──────────────────────────────────────

_DEFAULT_EMBED_URL = "https://api.mistral.ai/v1/embeddings"
_DEFAULT_EMBED_MODEL = "mistral-embed"
_EMBED_DIM = 1024  # mistral-embed


def _get_env_key() -> str:
    """Get the embedding API key from environment.

    Tries: MISTRAL_API_KEY → OPENAI_API_KEY
    """
    for key in ("MISTRAL_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(key)
        if val and len(val) > 10:
            return val
    # Try .env file
    env_path = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / ".env"
    if env_path.exists():
        content = env_path.read_text()
        for line in content.splitlines():
            ls = line.strip()
            if "=" in ls and not ls.startswith("#"):
                k, v = ls.split("=", 1)
                if k in ("MISTRAL_API_KEY", "OPENAI_API_KEY"):
                    return v.strip()
    return ""


def _api_embed(
    texts: list[str], *, api_key: str | None = None
) -> list[list[float]] | None:
    """Embed a list of texts via the Mistral API.

    Returns list of vectors or None on failure.
    """
    key = api_key or _get_env_key()
    if not key:
        return None

    import urllib.error
    import urllib.request

    body = json.dumps({"model": _DEFAULT_EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        _DEFAULT_EMBED_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        embeds = sorted(result["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeds]
    except Exception as e:
        logger.debug("Embedding API call failed: %s", e)
        return None


def _test_api(*, api_key: str) -> bool:
    """Quick connectivity test to the embedding API."""
    result = _api_embed(["test"], api_key=api_key)
    return result is not None


# ── Local model ──────────────────────────────────────────────────────────


def _get_model(model_name: str = "all-MiniLM-L6-v2"):
    """Load sentence-transformers model (requires PyTorch)."""
    global _MODEL
    if _MODEL is None and _HAS_SENTENCE_TRANSFORMERS:
        try:
            _MODEL = SentenceTransformer(model_name)
            logger.info(
                "Embedding model loaded: %s (dim=%d)",
                model_name,
                _MODEL.get_sentence_embedding_dimension(),
            )
        except Exception as e:
            logger.warning("Failed to load embedding model %s: %s", model_name, e)
    return _MODEL


# ── Schema ────────────────────────────────────────────────────────────────

_EMBED_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fact_embeddings_v2 (
    fact_id     INTEGER PRIMARY KEY REFERENCES facts(fact_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'local',  -- 'local' (sentence-transformers) or 'api' (mistral)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ── EmbeddingIndex ────────────────────────────────────────────────────────


class EmbeddingIndex:
    """Vector embedding index with dual provider support.

    Tries sentence-transformers first (when available). Falls back to
    API-based embeddings (Mistral) automatically.

    Implements model binding (Memvid inspiration): once facts are indexed
    with a provider/model, queries use the same model to avoid dimension
    or distribution mismatch.
    """

    def __init__(self, db_path: Path, model_name: str = ""):
        self.db_path = db_path
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, timeout=10.0
        )
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.debug("WAL mode not available (non-fatal)")
        self._conn.execute(_EMBED_TABLE_SQL)
        self._conn.commit()

        # Provider selection
        self._sentence_model = None
        self._api_key: str | None = None
        if model_name:
            self._sentence_model = _get_model(model_name)
        if not self._sentence_model:
            self._api_key = _get_env_key()
            if self._api_key:
                if not _test_api(api_key=self._api_key):
                    logger.warning(
                        "Embedding API test failed — vector search unavailable"
                    )
                    self._api_key = None

        # Detect existing provider
        self._provider, self._model, self._dim = self._detect_existing_provider()

        self.available = self._sentence_model is not None or self._api_key is not None

    def _detect_existing_provider(self) -> tuple[str | None, str | None, int]:
        """Detect the provider/model/dim of existing indexed facts.

        Returns (provider, model, dim) of the most recent indexed fact,
        or (None, None, 0) if the table is empty.
        """
        row = self._conn.execute(
            "SELECT provider, model, dim FROM fact_embeddings_v2 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["provider"], row["model"], int(row["dim"])
        return None, None, 0

    @property
    def dim(self) -> int:
        if self._sentence_model:
            return self._sentence_model.get_sentence_embedding_dimension()
        if self._dim:
            return self._dim
        return _EMBED_DIM

    def _encode(self, text: str) -> list[float] | None:
        """Encode text using the best available provider."""
        if self._sentence_model:
            try:
                return self._sentence_model.encode(
                    text, normalize_embeddings=True
                ).tolist()
            except Exception as e:
                logger.debug("Local embedding encode failed: %s", e)
        if self._api_key:
            result = _api_embed([text], api_key=self._api_key)
            if result:
                return result[0]
        return None

    def index_fact(self, fact_id: int, content: str) -> bool:
        """Compute and store embedding for a fact. Returns True if stored."""
        if not self.available:
            return False
        vec = self._encode(content)
        if vec is None:
            return False
        np_mod = _get_np()
        if np_mod is None:
            return False
        blob = np_mod.array(vec, dtype=np_mod.float32).tobytes()
        provider = "local" if self._sentence_model else "api"
        model = (
            self._sentence_model.model_name
            if self._sentence_model
            else _DEFAULT_EMBED_MODEL
        )
        dim = len(vec)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO fact_embeddings_v2 (fact_id, embedding, model, dim, provider) VALUES (?, ?, ?, ?, ?)",
                (fact_id, blob, model, dim, provider),
            )
            self._conn.commit()
        return True

    def remove_fact(self, fact_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM fact_embeddings_v2 WHERE fact_id = ?", (fact_id,)
            )
            self._conn.commit()

    def search(
        self, query: str, fact_ids: list[int], limit: int = 10
    ) -> dict[int, float]:
        """Search facts by embedding similarity. Returns {fact_id: cosine_similarity}.

        Flat brute-force scan using numpy — fine for eling's scale (<10k facts).
        For larger scales, swap in hnswlib/faiss.
        """
        if not self.available or not fact_ids:
            return {}
        qvec = self._encode(query)
        if qvec is None:
            return {}

        np_mod = _get_np()
        if np_mod is None:
            return {}
        qvec_np = np_mod.array(qvec, dtype=np_mod.float32)
        placeholders = ",".join("?" for _ in fact_ids)
        rows = self._conn.execute(
            f"SELECT fact_id, embedding FROM fact_embeddings_v2 WHERE fact_id IN ({placeholders})",
            fact_ids,
        ).fetchall()

        scores: dict[int, float] = {}
        for fid, blob in rows:
            try:
                fvec = np_mod.frombuffer(blob, dtype=np_mod.float32)
                sim = float(np_mod.dot(qvec_np, fvec))  # cosine (normalized)
                scores[int(fid)] = sim
            except Exception:
                continue

        return scores

    def reindex_all(self, facts: list[tuple[int, str]]) -> dict:
        """Batch reindex a list of (fact_id, content) tuples.

        Returns stats: {indexed, failed}
        """
        indexed = 0
        failed = 0
        for fid, content in facts:
            if self.index_fact(fid, content):
                indexed += 1
            else:
                failed += 1
        return {"indexed": indexed, "failed": failed}

    def stats(self) -> dict:
        """Return embedding index statistics."""
        if not self.available:
            return {"available": False, "model": None, "indexed_facts": 0, "dim": 0}
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) as n FROM fact_embeddings_v2"
            ).fetchone()[0]
            provider = self._conn.execute(
                "SELECT provider, COUNT(*) as n FROM fact_embeddings_v2 GROUP BY provider ORDER BY n DESC LIMIT 1"
            ).fetchone()
        provider_name = provider["provider"] if provider else "none"
        return {
            "available": True,
            "model": self._model or _DEFAULT_EMBED_MODEL,
            "dim": self.dim,
            "indexed_facts": int(count),
            "provider": provider_name,
        }

    def close(self):
        self._conn.close()

"""Prototype 1: API-based vector embeddings + HNSW-indexed vector search.

Inspired by Memvid's vec + api_embed features.
Uses OpenCode Zen API for embeddings (lightweight, no pytorch/onnx needed).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── API Embedding Provider ──────────────────────────────────────────────────

_DEFAULT_EMBED_URL = "https://api.mistral.ai/v1/embeddings"
_DEFAULT_EMBED_MODEL = "mistral-embed"
_EMBED_DIM = 1024  # mistral-embed uses 1024 dimensions


def _get_env_key() -> str:
    """Get the embedding API key from environment.

    Tries: MISTRAL_API_KEY → OPENAI_API_KEY → OPENCODE_ZEN_API_KEY
    (OpenAI quota exhausted, Mistral is preferred)
    """
    for key in ("MISTRAL_API_KEY", "OPENAI_API_KEY", "OPENCODE_ZEN_API_KEY"):
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


def _api_embed(texts: list[str], api_key: str | None = None,
               url: str = _DEFAULT_EMBED_URL,
               model: str = _DEFAULT_EMBED_MODEL) -> list[list[float]] | None:
    """Get embeddings from any OpenAI-compatible API endpoint.

    Returns list of vectors or None on failure.
    """
    import urllib.request
    import urllib.error

    key = api_key or _get_env_key()
    if not key:
        logger.debug("No API key found for embeddings")
        return None

    data = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
        result = json.loads(body)
        # Parse OpenAI-compatible response
        # Sort by index to ensure ordering
        embeds = sorted(result["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeds]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("Embedding API returned non-JSON/malformed response: %s", e)
        return None
    except urllib.error.HTTPError as e:
        logger.debug("Embedding API HTTP %s: %s", e.code, e.reason)
        return None
    except Exception as e:
        logger.debug("Embedding API call failed: %s", e)
        return None


# ── Local Embedding Cache (numpy flat index) ────────────────────────────────

class VectorIndex:
    """Simple numpy flat index for cosine similarity search.

    For the prototype this uses brute-force (fine for <10K vectors).
    Can be replaced with HNSW (via hnswlib) when available.
    """

    def __init__(self, dim: int = _EMBED_DIM):
        self.dim = dim
        self._vectors: list[np.ndarray] = []
        self._ids: list[int] = []

    def add(self, fact_id: int, vector: list[float]) -> None:
        self._vectors.append(np.array(vector, dtype=np.float32))
        self._ids.append(fact_id)

    def remove(self, fact_id: int) -> None:
        try:
            idx = self._ids.index(fact_id)
            self._vectors.pop(idx)
            self._ids.pop(idx)
        except ValueError:
            pass

    def search(self, query_vec: list[float], top_k: int = 10,
               ids_filter: set[int] | None = None) -> list[tuple[int, float]]:
        """Search by cosine similarity. Returns [(fact_id, score)]."""
        if not self._vectors:
            return []
        q = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        # Build candidate list
        if ids_filter:
            idxs = [i for i, fid in enumerate(self._ids) if fid in ids_filter]
        else:
            idxs = list(range(len(self._vectors)))

        if not idxs:
            return []

        mat = np.array([self._vectors[i] for i in idxs], dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1)
        # Avoid division by zero
        norms[norms == 0] = 1.0
        mat = mat / norms[:, np.newaxis]

        scores = mat @ q  # dot product = cosine for normalized vectors
        top_n = min(top_k, len(idxs))
        top_indices = np.argpartition(scores, -top_n)[-top_n:]
        # Sort by score descending
        order = np.argsort(-scores[top_indices])
        return [
            (self._ids[idxs[top_indices[i]]], float(scores[top_indices[i]]))
            for i in order
        ]

    def clear(self) -> None:
        self._vectors.clear()
        self._ids.clear()

    @property
    def size(self) -> int:
        return len(self._ids)


# ── Vector-Enhanced Embedding Index ────────────────────────────────────────

_EMBED_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fact_embeddings_v2 (
    fact_id     INTEGER PRIMARY KEY REFERENCES facts(fact_id) ON DELETE CASCADE,
    embedding   TEXT NOT NULL,            -- JSON array of floats
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_VEC_SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS vec_index_meta (
    model       TEXT PRIMARY KEY,
    dim         INTEGER NOT NULL,
    indexed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class VectorSearchLayer:
    """Prototype: API-based vector search layer for eling facts.

    Features:
    - OpenAI-compatible API embeddings (no pytorch needed)
    - Numpy flat index for cosine similarity
    - Model binding (prevent embedding model mismatch)
    - Tags as structured key-value pairs
    - Stats tracking

    Usage:
        vsl = VectorSearchLayer(db_path, api_key=...)
        vsl.index_fact(1, "Hello world")
        results = vsl.search("hello", top_k=5)
    """

    def __init__(
        self,
        db_path: str | Path,
        api_key: str | None = None,
        embed_url: str = _DEFAULT_EMBED_URL,
        model: str = _DEFAULT_EMBED_MODEL,
        dim: int = _EMBED_DIM,
    ):
        self.db_path = Path(db_path).expanduser()
        self.embed_url = embed_url
        self.model = model
        self.dim = dim
        self.api_key = api_key or _get_env_key()

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._conn.executescript(_EMBED_TABLE_SQL)
        self._conn.executescript(_VEC_SEARCH_SCHEMA)

        # In-memory numpy index
        self._index = VectorIndex(dim=dim)
        self._load_index()
        self.available = self.api_key != "" and self._test_api()

        logger.info(
            "VectorSearchLayer: model=%s dim=%d api=%s available=%s indexed=%d",
            model, dim, embed_url, self.available, self._index.size,
        )

    def _test_api(self) -> bool:
        """Quick test that the embedding API works."""
        result = _api_embed(["test"], self.api_key, self.embed_url, self.model)
        return result is not None and len(result) == 1 and len(result[0]) == self.dim

    def _load_index(self) -> None:
        """Load all embeddings from DB into the numpy index."""
        rows = self._conn.execute(
            "SELECT fact_id, embedding FROM fact_embeddings_v2 WHERE model = ?",
            (self.model,),
        ).fetchall()
        for row in rows:
            vec = json.loads(row["embedding"])
            self._index.add(int(row["fact_id"]), vec)

    def _bind_model(self) -> str | None:
        """Check/record model binding. Returns error message or None."""
        row = self._conn.execute(
            "SELECT model FROM vec_index_meta LIMIT 1"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO vec_index_meta (model, dim) VALUES (?, ?)",
                (self.model, self.dim),
            )
            self._conn.commit()
            return None
        existing = row["model"]
        if existing != self.model:
            return (
                f"Model mismatch: index was created with '{existing}' "
                f"but you're querying with '{self.model}'. "
                f"Use VecSearchLayer(db_path, model='{existing}')"
            )
        return None

    # ── CRUD ──

    def index_fact(self, fact_id: int, content: str) -> bool:
        """Compute embedding and add to index. Returns True on success."""
        if not content.strip():
            return False
        if not self.available:
            logger.debug("Embedding API unavailable, skipping index")
            return False

        result = _api_embed([content], self.api_key, self.embed_url, self.model)
        if result is None or len(result) == 0:
            return False
        vec = result[0]

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO fact_embeddings_v2 "
                "(fact_id, embedding, model, dim) VALUES (?, ?, ?, ?)",
                (fact_id, json.dumps(vec), self.model, len(vec)),
            )
            self._conn.commit()
            self._index.add(fact_id, vec)
        return True

    def remove_fact(self, fact_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM fact_embeddings_v2 WHERE fact_id = ?", (fact_id,))
            self._conn.commit()
            self._index.remove(fact_id)

    def reindex_all(self, fact_id_content_pairs: list[tuple[int, str]]) -> dict:
        """Reindex all facts in batch. Returns stats."""
        ok = 0
        fail = 0
        # Batch embed in chunks of 20
        batch_size = 20
        for i in range(0, len(fact_id_content_pairs), batch_size):
            batch = fact_id_content_pairs[i:i + batch_size]
            texts = [c for _, c in batch]
            results = _api_embed(texts, self.api_key, self.embed_url, self.model)
            if results is None:
                fail += len(batch)
                continue
            with self._lock:
                for (fid, _), vec in zip(batch, results):
                    self._conn.execute(
                        "INSERT OR REPLACE INTO fact_embeddings_v2 "
                        "(fact_id, embedding, model, dim) VALUES (?, ?, ?, ?)",
                        (fid, json.dumps(vec), self.model, len(vec)),
                    )
                    self._index.add(fid, vec)
                self._conn.commit()
            ok += len(results)
        return {"indexed": ok, "failed": fail}

    # ── Search ──

    def search(self, query: str, top_k: int = 10,
               fact_ids: list[int] | None = None) -> list[dict]:
        """Semantic search by vector similarity.

        Args:
            query: Natural language query
            top_k: Max results
            fact_ids: Optional filter to specific fact IDs (post-FTS5 filter)

        Returns:
            [{"fact_id": int, "score": float, ...}]
        """
        if not self.available or not query.strip():
            return []

        bind_err = self._bind_model()
        if bind_err:
            logger.warning(bind_err)
            return []

        result = _api_embed([query], self.api_key, self.embed_url, self.model)
        if result is None:
            return []
        qvec = result[0]

        ids_filter = set(fact_ids) if fact_ids else None
        hits = self._index.search(qvec, top_k=top_k, ids_filter=ids_filter)

        return [
            {"fact_id": fid, "score": round(score, 4), "vector_model": self.model}
            for fid, score in hits
        ]

    # ── Stats ──

    def stats(self) -> dict:
        return {
            "available": self.available,
            "model": self.model,
            "dim": self.dim,
            "indexed_facts": self._index.size,
            "api_url": self.embed_url,
            "model_bound": self._conn.execute(
                "SELECT model FROM vec_index_meta LIMIT 1"
            ).fetchone() is not None,
        }

    def close(self) -> None:
        self._conn.close()

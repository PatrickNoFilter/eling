"""Optional vector embedding layer for semantic search.

Uses sentence-transformers when available (pip install eling[embeddings]).
Falls back gracefully when not installed — the facts layer just skips
embedding scoring.
"""

from __future__ import annotations

import logging
import pickle
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HAS_SENTENCE_TRANSFORMERS = False
_MODEL = None

try:
    from sentence_transformers import SentenceTransformer

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore


def _get_model(model_name: str = "all-MiniLM-L6-v2"):
    global _MODEL
    if _MODEL is None and _HAS_SENTENCE_TRANSFORMERS:
        try:
            _MODEL = SentenceTransformer(model_name)
            logger.info("Embedding model loaded: %s (dim=%d)", model_name, _MODEL.get_sentence_embedding_dimension())
        except Exception as e:
            logger.warning("Failed to load embedding model %s: %s", model_name, e)
    return _MODEL


_EMBED_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fact_embeddings (
    fact_id   INTEGER PRIMARY KEY REFERENCES facts(fact_id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class EmbeddingIndex:
    """Optional vector embedding index for facts.

    Stores sentence-transformer embeddings in a separate table.
    All operations are no-ops when sentence-transformers is not installed.
    """

    def __init__(self, db_path: Path, model_name: str = "all-MiniLM-L6-v2"):
        self.db_path = db_path
        self.model_name = model_name
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10.0)
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(_EMBED_TABLE_SQL)
        self._conn.commit()
        self._model = _get_model(model_name)
        self.available = self._model is not None

    @property
    def dim(self) -> int:
        if self._model:
            return self._model.get_sentence_embedding_dimension()
        return 0

    def encode(self, text: str) -> list[float] | None:
        """Encode text to embedding vector. Returns None if model unavailable."""
        if not self._model:
            return None
        try:
            return self._model.encode(text, normalize_embeddings=True).tolist()
        except Exception as e:
            logger.debug("Embedding encode failed: %s", e)
            return None

    def index_fact(self, fact_id: int, content: str) -> bool:
        """Compute and store embedding for a fact. Returns True if stored."""
        if not self.available:
            return False
        vec = self.encode(content)
        if vec is None:
            return False
        blob = pickle.dumps(vec)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO fact_embeddings (fact_id, embedding, model, dim) VALUES (?, ?, ?, ?)",
                (fact_id, blob, self.model_name, len(vec)),
            )
            self._conn.commit()
        return True

    def remove_fact(self, fact_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM fact_embeddings WHERE fact_id = ?", (fact_id,))
            self._conn.commit()

    def search(self, query: str, fact_ids: list[int], limit: int = 10) -> dict[int, float]:
        """Search facts by embedding similarity. Returns {fact_id: cosine_similarity}."""
        if not self.available or not fact_ids:
            return {}
        qvec = self.encode(query)
        if qvec is None:
            return {}

        import numpy as np
        qvec_np = np.array(qvec, dtype=np.float32)
        placeholders = ",".join("?" for _ in fact_ids)
        rows = self._conn.execute(
            f"SELECT fact_id, embedding FROM fact_embeddings WHERE fact_id IN ({placeholders})",
            fact_ids,
        ).fetchall()

        scores: dict[int, float] = {}
        for fid, blob in rows:
            try:
                fvec = np.array(pickle.loads(blob), dtype=np.float32)
                sim = float(np.dot(qvec_np, fvec))  # cosine (normalized)
                scores[int(fid)] = sim
            except Exception:
                continue

        return scores

    def stats(self) -> dict:
        """Return embedding index statistics."""
        if not self.available:
            return {"available": False, "model": None, "indexed_facts": 0, "dim": 0}
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) as n FROM fact_embeddings").fetchone()[0]
        return {
            "available": True,
            "model": self.model_name,
            "dim": self.dim,
            "indexed_facts": int(count),
        }

    def close(self):
        self._conn.close()

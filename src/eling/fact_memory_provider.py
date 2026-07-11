"""Fact Memory Provider — standalone memory provider for the facts (1st) layer.

Wraps ``FactsLayer`` with a clean ``remember``/``recall``/``forget``/``probe``
interface that any agent framework (Hermes, OpenCode, Claude CLI, etc.) can
adopt without importing the full eling Brain.

Key design
----------
* **Standalone** — no Brain dependency.  Give it a ``db_path`` and it works.
* **Hermes-compatible** — exposes Hermes-format tool schemas + ``register(registry)``
  so Hermes can load it as a plugin.
* **Privacy-first** — runs the privacy pipeline (PII redaction, SHA-256 dedup) on every
  ``remember`` call.
* **Lazy init** — the underlying ``FactsLayer`` (SQLite + HRR + BM25 + Jaccard +
  optional embedding index) is created on first use, not at construction.
* **Decay-ready** — exposes ``apply_decay()`` for the forgetting-curve engine.

Usage
-----
::

    from eling.fact_memory_provider import FactMemoryProvider

    mem = FactMemoryProvider(db_path="~/.myagent/facts.db")
    mem.remember("Alice loves chocolate", category="preference")
    results = mem.recall("Alice")
    mem.forget(42)
    mem.close()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .layers.facts import FactsLayer
from .privacy import PrivacyPipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hermes-compatible tool schemas (name + description + parameters)
# ---------------------------------------------------------------------------

FACT_REMEMBER_SCHEMA = {
    "name": "fact_remember",
    "description": "Store an atomic fact in memory (1st layer). "
    "Runs PII redaction and SHA-256 dedup before storing.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to store (short, atomic).",
            },
            "category": {
                "type": "string",
                "default": "general",
                "description": "Category — e.g. preference, config, project, user.",
            },
            "tags": {
                "type": "string",
                "default": "",
                "description": "Comma-separated tags for retrieval.",
            },
            "source": {
                "type": "string",
                "default": "agent",
                "description": "Agent source identifier (hermes, opencode, …).",
            },
        },
        "required": ["content"],
    },
}

FACT_RECALL_SCHEMA = {
    "name": "fact_recall",
    "description": "Search stored facts via BM25 + Jaccard + HRR hybrid retrieval. "
    "Returns scored results sorted by relevance × trust.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "category": {
                "type": "string",
                "default": "",
                "description": "Optional category filter.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Max results.",
            },
            "min_trust": {
                "type": "number",
                "default": 0.3,
                "description": "Minimum trust score filter [0–1].",
            },
        },
        "required": ["query"],
    },
}

FACT_FORGET_SCHEMA = {
    "name": "fact_forget",
    "description": "Delete a fact by its numeric ID. Returns true on success.",
    "parameters": {
        "type": "object",
        "properties": {
            "fact_id": {
                "type": "integer",
                "description": "Fact ID to delete.",
            },
        },
        "required": ["fact_id"],
    },
}

FACT_PROBE_SCHEMA = {
    "name": "fact_probe",
    "description": "Look up all facts mentioning a specific entity. "
    "Falls back to full-text search if no entity match.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Entity name (person, project, library, …).",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Max results.",
            },
        },
        "required": ["entity"],
    },
}

FACT_STATS_SCHEMA = {
    "name": "fact_stats",
    "description": "Memory health statistics: total facts, entities, categories, "
    "active/dormant/cleared counts, pending contradictions.",
    "parameters": {"type": "object", "properties": {}},
}

FACT_EVOLVE_SCHEMA = {
    "name": "fact_evolve",
    "description": "Merge near-duplicate facts (Jaccard ≥ threshold). "
    "Combines content, averages trust, merges entities and links.",
    "parameters": {
        "type": "object",
        "properties": {
            "threshold": {
                "type": "number",
                "default": 0.65,
                "description": "Jaccard similarity threshold for merging [0–1].",
            },
        },
        "required": [],
    },
}

FACT_ENTITY_NEIGHBORS_SCHEMA = {
    "name": "fact_entity_neighbors",
    "description": "Return entities most strongly connected to a given entity "
    "via co-occurrence in facts.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Entity name to find neighbors for.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Max neighbors.",
            },
        },
        "required": ["entity"],
    },
}

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FactMemoryProvider:
    """Standalone memory provider wrapping the facts (1st) layer.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.  Expanded with ``~``.
    hrr_dim:
        Dimensionality of HRR vectors (default 1024).  Ignored when numpy
        is not available.
    embedding_model:
        Optional sentence-transformer model name for semantic embeddings.
    default_trust:
        Default trust score assigned to new facts [0–1].
    """

    def __init__(
        self,
        db_path: str | Path = "~/.eling/facts.db",
        hrr_dim: int = 1024,
        embedding_model: str = "",
        default_trust: float = 0.5,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.hrr_dim = hrr_dim
        self.embedding_model = embedding_model
        self.default_trust = default_trust

        self._facts: FactsLayer | None = None
        self._privacy = PrivacyPipeline()

        # Hermes tool registry — set by register()
        self._registry: Any = None

    # ------------------------------------------------------------------
    # Lazy FactsLayer
    # ------------------------------------------------------------------

    @property
    def facts(self) -> FactsLayer:
        """Lazy-initialized ``FactsLayer`` instance."""
        if self._facts is None:
            self._facts = FactsLayer(
                db_path=self.db_path,
                hrr_dim=self.hrr_dim,
                embedding_model=self.embedding_model,
                default_trust=self.default_trust,
            )
        return self._facts

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
        source: str = "agent",
    ) -> dict:
        """Store a fact.  Runs privacy pipeline (redact + dedup) first.

        Returns
        -------
        dict with keys ``stored`` (bool), ``fact_id`` (int | None),
        ``content_preview``, ``redacted``, and ``duplicate`` (bool).
        """
        pp = self._privacy.process(content)
        if pp["is_duplicate"]:
            return {
                "stored": False,
                "duplicate": True,
                "message": "content already stored (SHA-256 dedup)",
                "redacted": pp["redacted"],
            }

        clean = pp["clean"]
        fact_id = self.facts.add(clean, category=category, tags=tags, source=source)

        return {
            "stored": True,
            "fact_id": int(fact_id),
            "content_preview": clean[:120],
            "redacted": pp["redacted"],
            "duplicate": False,
        }

    def recall(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
        min_trust: float = 0.3,
    ) -> list[dict]:
        """Hybrid search: BM25 + Jaccard + HRR + optional embedding."""
        cat = category or None
        return self.facts.search(query, category=cat, min_trust=min_trust, limit=limit)

    def forget(self, fact_id: int) -> bool:
        """Delete a fact by its ID.  Returns ``True`` if deleted."""
        return self.facts.remove(fact_id)

    def probe(self, entity: str, limit: int = 10) -> list[dict]:
        """All facts about a single entity.  Falls back to FTS."""
        return self.facts.probe(entity, limit=limit)

    def search(self, query: str, **kwargs: Any) -> list[dict]:
        """Alias for :meth:`recall`."""
        return self.recall(query, **kwargs)

    def stats(self) -> dict:
        """Memory health statistics."""
        return self.facts.stats()

    def evolve(self, threshold: float | None = None) -> dict:
        """Merge near-duplicate facts.  Returns merge counts."""
        return self.facts.evolve(threshold=threshold)

    def entity_neighbors(self, entity: str, limit: int = 10) -> list[dict]:
        """Entities most strongly connected via co-occurrence."""
        return self.facts.entity_neighbors(entity, limit=limit)

    def apply_decay(self, decay_rate: float = 0.01) -> dict:
        """Apply exponential forgetting curve.  Returns active/dormant/cleared counts."""
        return self.facts.apply_decay(decay_rate=decay_rate)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._facts is not None:
            self._facts.close()
            self._facts = None

    # ------------------------------------------------------------------
    # Hermes plugin interface
    # ------------------------------------------------------------------

    def register(self, registry: Any) -> None:
        """Hermes plugin entrypoint — register all fact provider tools.

        The *registry* object must expose ``register_tool(schema: dict,
        handler: Callable)``.

        After calling this, the Hermes agent can use ``fact_remember``,
        ``fact_recall``, ``fact_forget``, ``fact_probe``, ``fact_stats``,
        ``fact_evolve``, and ``fact_entity_neighbors``.
        """
        self._registry = registry

        bindings: list[tuple[dict, Callable]] = [
            (FACT_REMEMBER_SCHEMA, lambda **kw: self.remember(**kw)),
            (FACT_RECALL_SCHEMA, lambda **kw: self.recall(**kw)),
            (FACT_FORGET_SCHEMA, lambda **kw: self.forget(**kw)),
            (FACT_PROBE_SCHEMA, lambda **kw: self.probe(**kw)),
            (FACT_STATS_SCHEMA, lambda **kw: self.stats()),
            (FACT_EVOLVE_SCHEMA, lambda **kw: self.evolve(**kw)),
            (FACT_ENTITY_NEIGHBORS_SCHEMA, lambda **kw: self.entity_neighbors(**kw)),
        ]

        for schema, handler in bindings:
            try:
                registry.register_tool(schema, handler)
            except Exception as exc:
                logger.warning(
                    "FactMemoryProvider: failed to register %s: %s", schema["name"], exc
                )

        logger.info("FactMemoryProvider registered %d tools", len(bindings))

    def unregister(self) -> None:
        """Remove all registered tools from the Hermes registry (if supported)."""
        if self._registry is not None:
            names = [schema["name"] for schema in _ALL_SCHEMAS]
            try:
                for name in names:
                    self._registry.unregister_tool(name)
            except Exception as exc:
                logger.debug("FactMemoryProvider unregister (non-fatal): %s", exc)
            self._registry = None


# Schemas list used by unregister
_ALL_SCHEMAS = [
    FACT_REMEMBER_SCHEMA,
    FACT_RECALL_SCHEMA,
    FACT_FORGET_SCHEMA,
    FACT_PROBE_SCHEMA,
    FACT_STATS_SCHEMA,
    FACT_EVOLVE_SCHEMA,
    FACT_ENTITY_NEIGHBORS_SCHEMA,
]

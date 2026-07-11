"""Hermes plugin glue — registers eling tools into Hermes agent.

Registers two groups of tools:
1. Full ``eling_*`` tools via ``Brain`` (all 5 layers, RRF fusion).
2. Lightweight ``fact_*`` tools via ``FactMemoryProvider`` (facts layer only,
   no Brain dependency, privacy pipeline, standalone db_path).

Use ``fact_remember`` / ``fact_recall`` when you only need fast, atomic
fact storage.  Use ``eling_remember`` / ``eling_recall`` when you need the
full 5-layer stack with RRF fusion across facts + KB + code + Notion.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_brain = None
_fact_provider: Any = None  # FactMemoryProvider | None (lazy import)


def _get_brain():
    global _brain
    if _brain is None:
        from .brain import Brain

        _brain = Brain()
    return _brain


def _get_fact_provider():
    global _fact_provider
    if _fact_provider is None:
        from .fact_memory_provider import FactMemoryProvider

        _fact_provider = FactMemoryProvider()
    return _fact_provider


# Tool schemas (Hermes format) — full eling stack
ELING_REMEMBER_SCHEMA = {
    "name": "eling_remember",
    "description": "Store content in the appropriate memory layer (facts/kb/notion). "
    "Auto-routes: short (<500 chars) → facts, long/markdown → KB, explicit layer=notion → Notion.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "layer": {"type": "string", "enum": ["auto", "facts", "kb", "notion"]},
            "category": {"type": "string"},
            "tags": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["content"],
    },
}

ELING_RECALL_SCHEMA = {
    "name": "eling_recall",
    "description": "Cross-layer search across all 5 memory layers with RRF fusion.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "layers": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
}


def register(registry: Any) -> None:
    """Hermes plugin entrypoint — registers both eling_* and fact_* tools."""
    errors: list[str] = []

    # ── Full Brain-based tools (all 5 layers) ─────────────────────────
    try:
        registry.register_tool(
            ELING_REMEMBER_SCHEMA, lambda **kw: _get_brain().remember(**kw)
        )
        registry.register_tool(
            ELING_RECALL_SCHEMA, lambda **kw: _get_brain().recall(**kw)
        )
        logger.info("eling: registered eling_remember + eling_recall")
    except Exception as e:
        errors.append(f"brain tools: {e}")
        logger.warning("eling brain tool registration failed: %s", e)

    # ── Standalone FactMemoryProvider tools (1st layer only) ──────────
    try:
        _get_fact_provider().register(registry)
        logger.info("eling: registered fact_* tools via FactMemoryProvider")
    except Exception as e:
        errors.append(f"fact provider: {e}")
        logger.warning("FactMemoryProvider registration failed: %s", e)

    if errors:
        logger.warning(
            "eling plugin registered with %d error(s): %s",
            len(errors),
            "; ".join(errors),
        )


def on_session_end(session: dict) -> None:
    """Called by Hermes at session end.  Flushes memory to disk."""
    try:
        brain = _get_brain()
        brain.sync(direction="flush")
    except Exception:
        logger.debug("session-end flush skipped (non-fatal)")
    try:
        if _fact_provider is not None:
            _fact_provider.close()
    except Exception:
        logger.debug("fact provider close skipped (non-fatal)")

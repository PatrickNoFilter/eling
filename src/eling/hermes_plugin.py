"""Hermes plugin glue — registers eling tools into Hermes agent."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_brain = None


def _get_brain():
    global _brain
    if _brain is None:
        from .brain import Brain
        _brain = Brain()
    return _brain


# Tool schemas (Hermes format)
ELING_REMEMBER_SCHEMA = {
    "name": "eling_remember",
    "description": "Store content in the appropriate memory layer (facts/kb/notion).",
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
    """Hermes plugin entrypoint."""
    try:
        registry.register_tool(ELING_REMEMBER_SCHEMA, lambda **kw: _get_brain().remember(**kw))
        registry.register_tool(ELING_RECALL_SCHEMA, lambda **kw: _get_brain().recall(**kw))
        logger.info("eling: registered eling_remember + eling_recall")
    except Exception as e:
        logger.warning("eling plugin registration failed: %s", e)


def on_session_end(session: dict) -> None:
    """Called by Hermes at session end. No-op for v0.1."""
    pass

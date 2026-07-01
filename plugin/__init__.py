"""Eling MemoryProvider plugin for Hermes.

Wraps Eling Brain as a full MemoryProvider (5-layer facts + KB + codegraph + Notion).
Replaces holographic with richer cross-layer recall, auto-hooks, and Notion bridge.

Config priority (high → low):
  1. `plugins.eling.*` in Hermes config.yaml
  2. `ELING_*` environment variables
  3. `~/.eling/config.json` persistent overrides
  4. Hardcoded defaults in eling.config
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

try:
    from eling.brain import Brain
    from eling.config import describe_config, resolve_config
    _HAS_ELING = True
except ImportError:
    _HAS_ELING = False
    Brain = None  # type: ignore[assignment]
    describe_config = lambda: {}  # type: ignore[assignment]
    resolve_config = lambda cfg={}: cfg  # type: ignore[assignment]

_hermes_plugin_config: dict | None = None


def _get_plugin_config() -> dict:
    """Read `plugins.eling` from Hermes config.yaml (one-shot)."""
    global _hermes_plugin_config
    if _hermes_plugin_config is not None:
        return _hermes_plugin_config
    try:
        from hermes_cli.config import cfg_get, load_config
        config = load_config()
        _hermes_plugin_config = cfg_get(config, "plugins", "eling", default={}) or {}
    except Exception:
        _hermes_plugin_config = {}
    return _hermes_plugin_config


# ── Tools ─────────────────────────────────────────────────────────────────────

ELING_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with 5-layer retrieval. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall with entity resolution, HRR, and FTS5.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store content in auto-selected layer (short→facts, long→KB).\n"
        "• search — Cross-layer query with RRF fusion across all memory layers.\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• stats — Memory health: facts, entities, KB chunks, HRR coverage.\n"
        "• update — Update trust score for a fact.\n"
        "• remove — Remove a fact by ID."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "stats", "update", "remove"],
            },
            "content": {"type": "string", "description": "Content to store (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {
                "type": "array", "items": {"type": "string"},
                "description": "Entity names for 'reason'.",
            },
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_score": {"type": "number", "description": "New trust score (0.0–1.0) for 'update'."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

ELING_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# ── MemoryProvider ────────────────────────────────────────────────────────────

class ElingMemoryProvider:
    """5-layer memory provider (facts + code + KB + Notion). Replaces holographic."""

    def __init__(self, config: dict | None = None):
        self._config = config or _get_plugin_config()
        self._brain: Optional[Brain] = None
        self._session_id = ""

    @property
    def name(self) -> str:
        return "eling"

    def is_available(self) -> bool:
        """Eling is always available (SQLite + optional deps)."""
        return _HAS_ELING

    def initialize(self, session_id: str, **kwargs) -> None:
        """Connect Eling Brain."""
        if not _HAS_ELING:
            raise RuntimeError("eling package not installed")
        # Resolve full config with layered fallback
        cfg = resolve_config(self._config)
        home = cfg["home"]
        self._brain = Brain(
            home=home,
            hrr_dim=cfg["hrr_dim"],
            notion_api_key=os.environ.get("NOTION_API_KEY"),
            project_path=os.environ.get("ELING_PROJECT_PATH"),
        )
        self._session_id = session_id
        logger.info("Eling initialized at %s (%d facts, %d KB sources)",
                     home,
                     self._brain.facts.stats().get("total_facts", 0),
                     self._brain.kb.stats().get("total_sources", 0))

        # ── auto-flush on startup ──
        if cfg.get("auto_sync_turns", True):
            try:
                self._brain.sync(direction="flush")
            except Exception:
                pass

    def system_prompt_block(self) -> str:
        if not self._brain:
            return ""
        stats = self._brain.stats()
        facts_n = stats.get("facts", {}).get("total_facts", 0)
        kb_n = stats.get("kb", {}).get("total_sources", 0)
        if facts_n == 0 and kb_n == 0:
            return (
                "# Eling Memory\n"
                "Active. Empty store — proactively store facts the user expects you to remember.\n"
                "Use `fact_store` to add/search/probe/reason across 5 memory layers.\n"
                "Use `fact_feedback` to train trust scores."
            )
        return (
            f"# Eling Memory\n"
            f"Active. {facts_n} facts + {kb_n} KB sources with HRR + FTS5 + entity resolution.\n"
            f"Use `fact_store` to search, probe entities, reason, or add facts.\n"
            f"Use `fact_feedback` to rate facts and train trust scores."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._brain or not query.strip():
            return ""
        try:
            results = self._brain.recall(query, limit=5)
            merged = results.get("merged", [])
            if not merged:
                return ""
            lines = []
            for r in merged:
                trust = r.get("trust_score", r.get("trust", 0.5))
                layer = r.get("_layer", r.get("source", "?"))
                content = r.get("content", "")[:200]
                lines.append(f"- [{trust:.1f}|{layer}] {content}")
            return "## Eling Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Eling prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Auto-store user and assistant messages during session."""
        if not self._brain:
            return
        try:
            if user_content and len(user_content) > 10:
                self._brain.remember(user_content, layer="facts", category="user_pref",
                                     source="sync_turn")
            if assistant_content and len(assistant_content) > 20:
                self._brain.remember(assistant_content, layer="kb", category="assistant_reply",
                                     source="sync_turn")
            # Flush to disk for durability
            self._brain.sync(direction="flush")
        except Exception as e:
            logger.debug("Eling sync_turn failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [ELING_STORE_SCHEMA, ELING_FEEDBACK_SCHEMA]

    def handle_tool_call(self, name: str, arguments: dict) -> dict:
        if name == "fact_store":
            return self._handle_fact_store(arguments)
        elif name == "fact_feedback":
            return self._handle_fact_feedback(arguments)
        return {"error": f"Unknown tool: {name}"}

    def shutdown(self) -> None:
        if self._brain:
            self._brain.close()
            self._brain = None

    def on_session_end(self, messages: List[Dict]) -> None:
        """Extract preference/decision patterns at session end."""
        if not self._brain:
            return
        extracted = 0
        import re
        _PREF_PATTERNS = [
            re.compile(r"(?:prefer|like|use|always|never)\s+\w+", re.I),
            re.compile(r"(?:my|our)\s+\w+\s+(?:is|are|should)", re.I),
        ]
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 10:
                continue
            for pattern in _PREF_PATTERNS:
                if pattern.search(content):
                    try:
                        self._brain.remember(content[:400], layer="facts", category="user_pref")
                        extracted += 1
                    except Exception:
                        pass
                    break
        if extracted:
            logger.info("Eling auto-extracted %d facts from conversation", extracted)

    def backup_paths(self) -> list[str]:
        if not self._brain:
            return []
        return [str(self._brain.facts.db_path)]

    # ── Internal dispatch ────────────────────────────────────────────────────

    def _handle_fact_store(self, args: dict) -> dict:
        action = args.get("action", "search")
        if action == "add":
            content = args.get("content", "")
            if not content:
                return {"error": "content required for add"}
            result = self._brain.remember(
                content=content,
                layer=args.get("layer", "auto"),
                category=args.get("category", "general"),
                tags=args.get("tags", ""),
                source="fact_store",
            )
            return {"result": result}

        if action == "search":
            query = args.get("query", "")
            if not query:
                return {"error": "query required for search"}
            limit = args.get("limit", 10)
            entity = args.get("entity")
            if entity:
                return self._brain.facts.probe(entity, limit=limit)
            return self._brain.recall(query, limit=limit)

        if action == "probe":
            entity = args.get("entity", "")
            if not entity:
                return {"error": "entity required for probe"}
            limit = args.get("limit", 10)
            return {"results": self._brain.facts.probe(entity, limit=limit)}

        if action == "related":
            entity = args.get("entity", "")
            if not entity:
                return {"error": "entity required for related"}
            return {"results": self._brain.facts.related(entity)}

        if action == "reason":
            entities = args.get("entities", [])
            if not entities:
                return {"error": "entities required for reason"}
            return {"results": self._brain.facts.reason(entities)}

        if action == "stats":
            return self._brain.stats()

        if action == "update":
            fact_id = args.get("fact_id")
            if not fact_id:
                return {"error": "fact_id required for update"}
            trust = args.get("trust_score", 0.5)
            self._brain.facts.set_trust(fact_id, float(trust))
            return {"success": True, "fact_id": fact_id, "trust_score": trust}

        if action == "remove":
            fact_id = args.get("fact_id")
            if not fact_id:
                return {"error": "fact_id required for remove"}
            ok = self._brain.facts.remove(int(fact_id))
            return {"success": ok}

        return {"error": f"Unknown action: {action}"}

    def _handle_fact_feedback(self, args: dict) -> dict:
        action = args.get("action", "")
        fact_id = args.get("fact_id")
        if not fact_id:
            return {"error": "fact_id required"}
        try:
            if action == "helpful":
                info = self._brain.facts.update_trust(fact_id, helpful=True)
                return {"success": True, "fact_id": fact_id, "trust_score": info.get("trust_score", 0)}
            elif action == "unhelpful":
                info = self._brain.facts.update_trust(fact_id, helpful=False)
                return {"success": True, "fact_id": fact_id, "trust_score": info.get("trust_score", 0)}
            return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": str(e)}


# ── Hermes plugin entrypoint ─────────────────────────────────────────────────

def register(registry) -> None:
    """Register Eling as a memory provider (called by Hermes plugin loader)."""
    provider = ElingMemoryProvider()
    registry.register_memory_provider(provider)
    logger.info("Eling memory provider registered")

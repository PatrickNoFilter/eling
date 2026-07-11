"""Eling hooks system — 12 lifecycle hooks for memory-aware agent behavior.

Architecture:
  HookRegistry maps hook names → list of handler functions.
  Brain emits hooks at key lifecycle points.
  Each handler receives a context dict + optional brain reference.

Hook lifecycle (order of fire):
  1. session_start       — Session begins
  2. pre_user_message    — Before processing user input
  3. post_user_message   — After user input indexed
  4. pre_tool_use        — Before tool call
  5. post_tool_use       — After tool call returns
  6. post_assistant_message — After assistant reply
  7. decision_made       — User correction/affirmation
  8. file_edit           — File modification detected
  9. error_occurred      — Tool/agent error
  10. compaction         — Context window compaction
  11. session_end        — Session ends
  12. idle_30min         — 30 min inactivity

Compatibility: handlers can be Hermes plugin lifecycle callbacks
or pure in-process Eling handlers.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .brain import Brain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hook name constants
# ---------------------------------------------------------------------------

HOOK_SESSION_START = "session_start"
HOOK_PRE_USER_MESSAGE = "pre_user_message"
HOOK_POST_USER_MESSAGE = "post_user_message"
HOOK_PRE_TOOL_USE = "pre_tool_use"
HOOK_POST_TOOL_USE = "post_tool_use"
HOOK_POST_ASSISTANT_MESSAGE = "post_assistant_message"
HOOK_DECISION_MADE = "decision_made"
HOOK_FILE_EDIT = "file_edit"
HOOK_ERROR_OCCURRED = "error_occurred"
HOOK_COMPACTION = "compaction"
HOOK_SESSION_END = "session_end"
HOOK_IDLE_30MIN = "idle_30min"

# ── Verify-on-stop hook ──
HOOK_VERIFY_REQUEST = "verify_request"

# ── Sync hooks ──
HOOK_SYNC_START = "sync_start"
HOOK_SYNC_COMPLETE = "sync_complete"
HOOK_SYNC_ERROR = "sync_error"

ALL_HOOKS = [
    HOOK_SESSION_START,
    HOOK_PRE_USER_MESSAGE,
    HOOK_POST_USER_MESSAGE,
    HOOK_PRE_TOOL_USE,
    HOOK_POST_TOOL_USE,
    HOOK_POST_ASSISTANT_MESSAGE,
    HOOK_DECISION_MADE,
    HOOK_FILE_EDIT,
    HOOK_VERIFY_REQUEST,
    HOOK_ERROR_OCCURRED,
    HOOK_COMPACTION,
    HOOK_SESSION_END,
    HOOK_IDLE_30MIN,
    HOOK_SYNC_START,
    HOOK_SYNC_COMPLETE,
    HOOK_SYNC_ERROR,
]

# ---------------------------------------------------------------------------
# Handler type
# ---------------------------------------------------------------------------

HookHandler = Callable[[str, dict], Any]
"""Handler signature: (hook_name, context_dict) -> Any (return value collected by fire())"""


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Thread-safe registry of named hook handlers.

    Each hook can have multiple handlers; all fire in registration order.
    """

    def __init__(self):
        self._handlers: dict[str, list[HookHandler]] = {h: [] for h in ALL_HOOKS}

    def register(self, hook_name: str, handler: HookHandler) -> None:
        """Register a handler for a hook. No-op for unknown hooks (log warning)."""
        if hook_name not in self._handlers:
            logger.warning("hook_registry: unknown hook %r, ignoring", hook_name)
            return
        self._handlers[hook_name].append(handler)

    def unregister(self, hook_name: str, handler: HookHandler) -> None:
        """Remove a specific handler from a hook."""
        if hook_name in self._handlers and handler in self._handlers[hook_name]:
            self._handlers[hook_name].remove(handler)

    def fire(self, hook_name: str, context: dict | None = None) -> list[Any]:
        """Fire all handlers for a hook. Returns list of return values.

        All exceptions are caught and logged (hooks never crash the caller).
        """
        if hook_name not in self._handlers:
            return []
        ctx = context or {}
        results: list[Any] = []
        for handler in self._handlers[hook_name]:
            try:
                result = handler(hook_name, ctx)
                results.append(result)
            except Exception as e:
                logger.exception(
                    "hook %s handler %s crashed: %s", hook_name, handler, e
                )
                results.append(None)
        return results

    def has_handlers(self, hook_name: str) -> bool:
        """Check if a hook has any registered handlers."""
        return hook_name in self._handlers and len(self._handlers[hook_name]) > 0

    @property
    def total_handlers(self) -> int:
        return sum(len(hs) for hs in self._handlers.values())

    def reset(self) -> None:
        """Remove all handlers (for testing)."""
        for h in self._handlers:
            self._handlers[h] = []


# ---------------------------------------------------------------------------
# Built-in handlers (the 12 default behaviors)
# ---------------------------------------------------------------------------


def _make_session_start_handler(brain: "Brain") -> HookHandler:
    """HOOK: session_start — load project profile, warm caches."""

    def handler(name: str, ctx: dict) -> dict:
        logger.info("hook: session_start — warming caches")
        info = {
            "facts_count": brain.facts.stats().get("total_facts", 0),
            "kb_sources": brain.kb.stats().get("total_sources", 0),
            "code_available": brain.code.available,
            "notion_available": brain.notion.available,
        }
        # Pre-warm: get top concepts from facts
        if brain.facts.stats().get("total_facts", 0) > 0:
            top = brain.facts.search("", limit=3)
            info["top_concepts"] = [r.get("content", "")[:80] for r in (top or [])]
        return info

    return handler


def _make_pre_user_message_handler(brain: "Brain") -> HookHandler:
    """HOOK: pre_user_message — inject relevant memories into context."""

    def handler(name: str, ctx: dict) -> dict:
        user_msg = ctx.get("content", "")
        if not user_msg:
            return {"injected": False, "reason": "no content"}
        # Call layers directly (not brain.recall()) to avoid re-entrant hook firing
        results = brain.facts.search(user_msg, limit=5)
        kb_results = brain.kb.search(user_msg, limit=3)
        all_results = []
        for r in results or []:
            r["_layer"] = "facts"
            all_results.append(r)
        for r in kb_results or []:
            r["_layer"] = "kb"
            all_results.append(r)
        return {
            "injected": True,
            "memories": all_results,
        }

    return handler


def _make_post_user_message_handler(brain: "Brain") -> HookHandler:
    """HOOK: post_user_message — index user prompt to KB."""

    def handler(name: str, ctx: dict) -> dict:
        content = ctx.get("content", "")
        source = ctx.get("source", "user_prompt")
        if not content:
            return {"indexed": False}
        fid = brain.facts.add(content, category="user_prompt", tags="", source=source)
        return {"indexed": True, "fact_id": fid}

    return handler


def _make_pre_tool_use_handler(brain: "Brain") -> HookHandler:
    """HOOK: pre_tool_use — recall context relevant to tool args."""

    def handler(name: str, ctx: dict) -> dict:
        tool_name = ctx.get("tool_name", "")
        args = ctx.get("arguments", "")
        # Build query from tool name + stringified args
        query = f"{tool_name} {str(args)[:200]}"
        if not query.strip():
            return {"recalled": False}
        # Use layer APIs directly to avoid re-entrant hooks
        results = brain.facts.search(query, limit=3)
        return {"recalled": True, "results": results or []}

    return handler


def _make_post_tool_use_handler(brain: "Brain") -> HookHandler:
    """HOOK: post_tool_use — dedup + privacy + store observation."""

    def handler(name: str, ctx: dict) -> dict:
        tool_name = ctx.get("tool_name", "")
        result = ctx.get("result", "")
        if not result and not tool_name:
            return {"stored": False}
        observation = f"Tool [{tool_name}] returned: {str(result)[:300]}"
        # Store directly to avoid re-entrant hook firing
        fid = brain.facts.add(observation, category="tool_observation", tags=tool_name)
        return {"stored": True, "fact_id": fid}

    return handler


def _make_post_assistant_message_handler(brain: "Brain") -> HookHandler:
    """HOOK: post_assistant_message — extract entities, store as facts."""

    def handler(name: str, ctx: dict) -> dict:
        content = ctx.get("content", "")
        if not content or len(content) < 20:
            return {"facts_stored": 0}
        # Store directly to avoid re-entrant hooks
        fid = brain.facts.add(content, category="assistant_reply", tags="")
        return {"facts_stored": 1, "fact_id": fid}

    return handler


def _make_decision_made_handler(brain: "Brain") -> HookHandler:
    """HOOK: decision_made — boost new fact trust, decay old contradicting ones."""

    def handler(name: str, ctx: dict) -> dict:
        content = ctx.get("content", "")
        correction = ctx.get("correction", "")
        if correction:
            # Index the correction as a high-trust fact
            fid = brain.facts.add(correction, category="correction", tags="decision")
            brain.facts.set_trust(fid, 0.95)
            return {"corrected": True, "fact_id": fid}

        if content:
            fid = brain.facts.add(content, category="decision", tags="")
            brain.facts.set_trust(fid, 0.9)
            return {"decided": True, "fact_id": fid}
        return {"decided": False}

    return handler


def _make_file_edit_handler(brain: "Brain") -> HookHandler:
    """HOOK: file_edit — re-index file in codegraph + track in verification ledger."""

    def handler(name: str, ctx: dict) -> dict:
        file_path = ctx.get("file_path", "")
        if not file_path:
            return {"reindexed": False, "verify_tracked": False}
        result: dict = {}
        # 1. Re-index in codegraph layer if available
        if brain.code.available:
            try:
                brain.code.reindex(file_path)
                result["reindexed"] = True
            except Exception:
                result["reindexed"] = False
        else:
            result["reindexed"] = False
        # 2. Track in verification ledger
        from . import verify_on_stop as vos

        adapter = getattr(brain, "_adapter", "auto")
        if not vos.host_has_verify_on_stop(adapter=adapter):
            vos.record_edit(file_path)
            result["verify_tracked"] = True
            # Fire verify_request hook
            brain.fire_hook(
                HOOK_VERIFY_REQUEST,
                changed_paths=list(vos._ledger.get("changed_paths", [])),
            )
        else:
            result["verify_tracked"] = False
        return result

    return handler


def _make_error_occurred_handler(brain: "Brain") -> HookHandler:
    """HOOK: error_occurred — store error + context for future avoidance."""

    def handler(name: str, ctx: dict) -> dict:
        error = ctx.get("error", "")
        tool = ctx.get("tool_name", "")
        context = ctx.get("context", "")
        content = f"ERROR [{tool}]: {error} | Context: {str(context)[:200]}"
        fid = brain.facts.add(content, category="error", tags=f"error,{tool}")
        return {"stored": True, "fact_id": fid}

    return handler


def _make_compaction_handler(brain: "Brain") -> HookHandler:
    """HOOK: compaction — snapshot session highlights → facts."""

    def handler(name: str, ctx: dict) -> dict:
        summary = ctx.get("summary", "")
        if not summary:
            return {"stored": False}
        fid = brain.facts.add(summary, category="session_summary", tags="compaction")
        return {"stored": True, "fact_id": fid}

    return handler


def _make_session_end_handler(brain: "Brain") -> HookHandler:
    """HOOK: session_end — summarize → Notion activity log."""

    def handler(name: str, ctx: dict) -> dict:
        summary = ctx.get("summary", "")
        if summary and brain.notion.available:
            page_id = brain.notion.create_page(
                title=f"📋 Session End — {time.strftime('%Y-%m-%d %H:%M')}",
                content=summary,
            )
            if page_id:
                return {"notion_page": page_id, "stored": True}
            # Fall through to local storage if Notion page creation failed
            logger.info(
                "session_end: notion page not created, falling back to local storage"
            )
        if summary:
            fid = brain.facts.add(
                summary, category="session_summary", tags="session_end"
            )
            return {"stored": True, "fact_id": fid}
        return {"logged": False}

    return handler


def _make_idle_30min_handler(brain: "Brain") -> HookHandler:
    """HOOK: idle_30min — snapshot, apply decay, sweep contradictions, promote."""

    def handler(name: str, ctx: dict) -> dict:
        # Snapshot before bulk operations
        snapshot_meta = {}
        try:
            snapshot_meta = brain.snapshot(reason="idle_30min_maintenance")
        except Exception as exc:
            logger.warning("idle_30min snapshot failed: %s", exc)

        # Apply forgetting decay first
        decay_result = brain.facts.apply_decay()

        # Periodic contradiction sweep: check recent un-flagged facts
        contradictions = brain.facts.detect_contradictions_for_unflagged(limit=20)

        # Memory evolution pass: merge near-duplicates
        evolution = brain.facts.evolve()

        promoted = 0
        if not brain.notion.available:
            return {
                "snapshot": snapshot_meta.get("snapshot_id", ""),
                "promoted": 0,
                "decay": decay_result,
                "contradictions": len(contradictions),
                "reason": "Notion not configured",
            }

        # Find high-trust (≥0.9) facts not yet in Notion
        high_trust = brain.facts.search("", min_trust=0.9, limit=10)
        parent_id = ctx.get("notion_parent_id")
        for fact in high_trust:
            fid = fact.get("fact_id")
            if fid and not fact.get("notion_page_id"):
                brain.reflect(fid, parent_page_id=parent_id)
                promoted += 1
        return {
            "snapshot": snapshot_meta.get("snapshot_id", ""),
            "promoted": promoted,
            "decay": decay_result,
            "contradictions": len(contradictions),
            "evolved": evolution.get("merged", 0),
        }

    return handler


def _make_verify_request_handler(brain: "Brain") -> HookHandler:
    """HOOK: verify_request — verification nudge for non-Hermes agents."""

    def handler(name: str, ctx: dict) -> dict:
        from . import verify_on_stop as vos

        # Skip if host agent has its own verify-on-stop
        adapter = getattr(brain, "_adapter", "auto")
        if vos.host_has_verify_on_stop(adapter=adapter):
            return {"nudge": None, "reason": "host has verify-on-stop"}

        changed = ctx.get("changed_paths", [])
        if not changed:
            return {"nudge": None, "reason": "no changed paths"}

        nudge = vos.build_verify_nudge()
        return {
            "nudge": nudge,
            "changed_paths_count": len(changed),
            "needs_verification": nudge is not None,
        }

    return handler


def _make_noop_handler(brain: "Brain" = None) -> HookHandler:  # type: ignore[assignment]
    """Factory: no-op handler for hooks with no default logic."""

    def handler(name: str, ctx: dict) -> dict:
        return {"handled": False, "hook": name}

    return handler


# ---------------------------------------------------------------------------
# Helper: register all default built-in handlers on a brain
# ---------------------------------------------------------------------------


def register_default_hooks(brain: "Brain") -> HookRegistry:
    """Create and populate a HookRegistry with all 15 built-in handlers."""
    registry = brain.hooks

    # Map hook name → factory function
    factories: dict[str, Callable] = {
        HOOK_SESSION_START: _make_session_start_handler,
        HOOK_PRE_USER_MESSAGE: _make_pre_user_message_handler,
        HOOK_POST_USER_MESSAGE: _make_post_user_message_handler,
        HOOK_PRE_TOOL_USE: _make_pre_tool_use_handler,
        HOOK_POST_TOOL_USE: _make_post_tool_use_handler,
        HOOK_POST_ASSISTANT_MESSAGE: _make_post_assistant_message_handler,
        HOOK_DECISION_MADE: _make_decision_made_handler,
        HOOK_FILE_EDIT: _make_file_edit_handler,
        HOOK_VERIFY_REQUEST: _make_verify_request_handler,
        HOOK_ERROR_OCCURRED: _make_error_occurred_handler,
        HOOK_COMPACTION: _make_compaction_handler,
        HOOK_SESSION_END: _make_noop_handler,
        HOOK_IDLE_30MIN: _make_idle_30min_handler,
        HOOK_SYNC_START: _make_noop_handler,
        HOOK_SYNC_COMPLETE: _make_noop_handler,
        HOOK_SYNC_ERROR: _make_noop_handler,
    }

    for hook_name, factory in factories.items():
        registry.register(hook_name, factory(brain))

    return registry

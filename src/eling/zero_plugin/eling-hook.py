#!/usr/bin/env python3
"""Eling auto-memory hook for Zero.

Receives hook payload on stdin (JSON), auto-stores facts in the local brain,
and captures telemetry events for eling-blackbox Layer 2 flight recorder.

Local memory layers (facts, KB) are served via the `as_brain` MCP server.
Notion sync is optional and handled separately via the `eling` MCP server.
Telemetry events are forwarded to the BlackboxStore for context-efficiency scoring.

Events handled:
  - afterTool    → store file edits as facts + capture telemetry
  - sessionStart → log session info, warm caches
  - sessionEnd   → flush memory to disk, optionally push to Notion
  - beforeTool   → recall relevant context for the tool
"""

import json
import logging
import os
import sys
import time

# ── Config ──────────────────────────────────────────────────────────────
ELING_HOME = os.environ.get("ELING_HOME", os.path.expanduser("~/.eling"))
BLACKBOX_ENABLED = os.environ.get("ELING_BLACKBOX_ENABLED", "1") == "1"
logging.basicConfig(
    level=logging.WARNING,
    format="[eling-hook] %(levelname)s %(message)s",
)
log = logging.getLogger("eling-hook")


def get_brain():
    """Lazy-init the Eling Brain."""
    from eling.brain import Brain

    return Brain(home=ELING_HOME)


def _get_blackbox():
    """Lazy-init the BlackboxStore."""
    if not BLACKBOX_ENABLED:
        return None
    try:
        from eling.blackbox.store import BlackboxStore

        db_path = os.environ.get("ELING_BLACKBOX_DB")
        return BlackboxStore(db_path=db_path) if db_path else BlackboxStore()
    except Exception as e:
        log.warning("blackbox not available: %s", e)
    return None


def _capture_telemetry(payload: dict, event_type_str: str) -> None:
    """Capture a telemetry event from a Zero hook payload."""
    bb = _get_blackbox()
    if bb is None:
        return
    try:
        from eling.blackbox.adapters.zero import parse_zero_event

        ev = parse_zero_event(payload, run_id=payload.get("sessionId", "zero:unknown"))
        if ev is not None:
            ev.timestamp = time.time()
            bb.ingest(ev)
    except Exception as e:
        log.debug("telemetry capture failed: %s", e)


def handle_before_tool(payload: dict) -> str | None:
    """Recall relevant context before a tool executes."""
    tool = payload.get("tool", "")
    args = payload.get("arguments") or payload.get("args", "")
    query = f"{tool} {str(args)[:200]}"
    if not query.strip():
        return None
    try:
        brain = get_brain()
        results = brain.recall(query, limit=3)
        merged = results.get("merged", [])
        if merged:
            return f"eling: recalled {len(merged)} memory items for {tool}"
    except Exception as e:
        log.warning("before_tool recall failed: %s", e)
    return None


def handle_after_tool(payload: dict) -> str | None:
    """Auto-store file changes and tool results as facts."""
    tool = payload.get("tool", "")
    status = payload.get("status", "")
    changed_files = payload.get("changedFiles") or payload.get("changed_files") or []
    session_id = payload.get("sessionId", "") or payload.get("session_id", "")
    result = payload.get("result", "") or payload.get("output", "")

    msgs = []
    brain = None

    # Capture blackbox telemetry
    _capture_telemetry(payload, "afterTool")

    # Remember file edits as facts
    if changed_files and status in ("success", "ok", "", None):
        try:
            brain = get_brain()
            for fp in changed_files[:5]:
                brain.remember(
                    f"Edited file: {fp}",
                    category="file_edit",
                    tags=tool,
                    source=f"zero:{session_id}" if session_id else "zero",
                )
            n = len(changed_files)
            msgs.append(f"eling: stored {n} file edit{'s' if n != 1 else ''}")
        except Exception as e:
            log.warning("after_tool store failed: %s", e)

    # Remember tool results as observations
    if result and tool and status in ("success", "ok", "", None):
        try:
            if brain is None:
                brain = get_brain()
            summary = str(result)[:300]
            brain.remember(
                f"Tool [{tool}] returned: {summary}",
                category="tool_observation",
                tags=tool,
                source=f"zero:{session_id}" if session_id else "zero",
            )
        except Exception as e:
            log.warning("after_tool store result failed: %s", e)

    return "\n".join(msgs) if msgs else None


def handle_session_start(payload: dict) -> str | None:
    """Session start: warm caches, log session info, start telemetry recording."""
    session_id = payload.get("sessionId", "") or payload.get("session_id", "")
    cwd = payload.get("cwd", "") or payload.get("working_dir", "")

    # Capture session start telemetry
    _capture_telemetry(payload, "sessionStart")

    log.info("Session start: %s in %s", session_id, cwd)
    try:
        brain = get_brain()
        stats = brain.stats()
        summary = (
            f"Session started — {stats.get('facts', {}).get('total_facts', 0)} facts, "
            f"{stats.get('kb', {}).get('total_sources', 0)} KB sources"
        )
        return f"eling: {summary}"
    except Exception as e:
        log.warning("session_start failed: %s", e)
    return None


def handle_session_end(payload: dict) -> str | None:
    """Session end: flush memory, sync to Notion, finalize telemetry."""
    try:
        brain = get_brain()
        brain.sync(direction="flush")
        msgs = ["eling: memory flushed to disk"]
        # Attempt Notion sync if available
        try:
            r = brain.sync(direction="push", layer="notion")
            pushed = r.get("pushed", 0)
            if pushed:
                msgs.append(f"eling: pushed {pushed} facts to Notion")
        except Exception:
            log.debug("notion push skipped (non-fatal)")
        return "\n".join(msgs)
    except Exception as e:
        log.warning("session_end sync failed: %s", e)
    return None


# ── Dispatch ────────────────────────────────────────────────────────────

EVENT_HANDLERS = {
    "beforeTool": handle_before_tool,
    "afterTool": handle_after_tool,
    "sessionStart": handle_session_start,
    "sessionEnd": handle_session_end,
}


def main():
    raw = sys.stdin.read()
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("invalid JSON payload: %.200s", raw)
        return

    event = payload.get("event", "")
    handler = EVENT_HANDLERS.get(event)
    if handler:
        try:
            result = handler(payload)
            if result:
                print(result, flush=True)
        except Exception as e:
            log.warning("handler for %s failed: %s", event, e)
    else:
        log.debug("no handler for event: %s", event)


if __name__ == "__main__":
    main()

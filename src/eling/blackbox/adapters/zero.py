"""Zero telemetry adapter — parses Zero's stream-JSON protocol.

Zero emits structured JSONL events via `zero exec --output-format stream-json`.
This adapter also creates a Zero plugin that pipes telemetry to eling-blackbox.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..core import (
    TraceEvent,
    EventType,
    RunSummary,
    AgentHost,
)
from .common import detect_project_key


# ── Zero stream-JSON event type → eling EventType mapping ────────────────

ZERO_EVENT_MAP: dict[str, EventType] = {
    "run_start": EventType.SESSION_START,
    "run_end": EventType.SESSION_END,
    "tool_call": EventType.TOOL_CALL,
    "tool_result": EventType.TOOL_RESULT,
    "permission_request": EventType.PERMISSION_REQUEST,
    "permission_decision": EventType.PERMISSION_DECISION,
    "usage": EventType.USAGE,
    "error": EventType.ERROR,
    "final": EventType.FINAL,
    "text": EventType.FINAL,  # mapped as a non-action event
}

# Tool name → EventType mapping for Zero tool calls
ZERO_TOOL_EVENT: dict[str, EventType] = {
    "read_file": EventType.READ_FILE,
    "list_directory": EventType.READ_FILE,
    "grep": EventType.READ_FILE,
    "glob": EventType.READ_FILE,
    "write_file": EventType.WRITE_FILE,
    "edit_file": EventType.EDIT_FILE,
    "apply_patch": EventType.EDIT_FILE,
    "bash": EventType.BASH,
    "Task": EventType.SUBAGENT_SPAWN,
}

SIDE_EFFECT_MAP: dict[str, str] = {
    "read": "read",
    "write": "write",
    "shell": "bash",
    "network": "network",
}


def parse_zero_event(raw: dict[str, Any], run_id: str) -> TraceEvent | None:
    """Convert a Zero stream-JSON event to canonical TraceEvent."""
    event_type = raw.get("type", "")
    canonical_type = ZERO_EVENT_MAP.get(event_type)
    if canonical_type is None:
        return None

    timestamp = time.time()
    agent_id = raw.get("agentId", "zero")
    session_id = raw.get("sessionId", "")
    run_id = run_id or raw.get("runId", f"zero_{int(timestamp)}")

    ev = TraceEvent(
        type=canonical_type,
        timestamp=timestamp,
        agent_id=agent_id,
        host=AgentHost.ZERO.value,
        run_id=run_id,
        session_id=session_id,
    )

    # Tool-specific mapping
    tool_name = raw.get("name", raw.get("tool", ""))
    args = raw.get("args", raw.get("arguments", {}))
    output = raw.get("output", raw.get("result", ""))

    if canonical_type == EventType.TOOL_CALL:
        ev.tool_name = tool_name
        ev.tool_args = args
        # Map tool name to action type
        action_type = ZERO_TOOL_EVENT.get(tool_name)
        if action_type:
            ev.type = action_type
            if action_type in (
                EventType.READ_FILE,
                EventType.WRITE_FILE,
                EventType.EDIT_FILE,
            ):
                ev.file_path = args.get("path", args.get("filePath", ""))
            elif action_type == EventType.BASH:
                ev.command = args.get("command", "")
            elif action_type == EventType.SUBAGENT_SPAWN:
                ev.subagent_role = args.get("name", "")
                ev.subagent_prompt = args.get("prompt", "")

        # Side effect type
        side_effect = raw.get("sideEffect", "")
        if side_effect in SIDE_EFFECT_MAP:
            ev.metadata["side_effect"] = SIDE_EFFECT_MAP[side_effect]

    elif canonical_type == EventType.TOOL_RESULT:
        ev.tool_name = tool_name
        ev.tool_output = output[:500] if output else ""
        ev.output_size = len(output) if output else 0
        ev.metadata["status"] = raw.get("status", "ok")

    elif canonical_type == EventType.PERMISSION_REQUEST:
        ev.tool_name = tool_name
        ev.permission_action = raw.get("action", "")
        ev.metadata["permission_mode"] = raw.get("permissionMode", "")

    elif canonical_type == EventType.PERMISSION_DECISION:
        ev.tool_name = tool_name
        ev.permission_granted = raw.get(
            "permissionGranted", raw.get("action") == "allow"
        )
        ev.permission_action = raw.get("action", "")

    elif canonical_type == EventType.USAGE:
        ev.prompt_tokens = raw.get("promptTokens", 0)
        ev.completion_tokens = raw.get("completionTokens", 0)
        ev.total_tokens = raw.get("totalTokens", 0)

    elif canonical_type == EventType.ERROR:
        ev.error_code = raw.get("code", "")
        ev.error_message = raw.get("message", "")
        ev.recoverable = raw.get("recoverable", False)

    elif canonical_type == EventType.FINAL:
        ev.metadata["text_length"] = len(raw.get("text", ""))

    elif canonical_type == EventType.SESSION_START:
        ev.agent_id = raw.get("agentId", agent_id)
        ev.metadata["cwd"] = raw.get("cwd", "")
        ev.metadata["provider"] = raw.get("provider", "")
        ev.metadata["model"] = raw.get("model", "")

    elif canonical_type == EventType.SESSION_END:
        ev.metadata["status"] = raw.get("status", "")
        ev.metadata["exit_code"] = raw.get("exitCode", 0)

    return ev


def build_summary_from_zero_events(events: list[TraceEvent]) -> RunSummary:
    """Aggregate a RunSummary from canonically-parsed Zero events."""
    if not events:
        return RunSummary(
            run_id="",
            session_id="",
            host=AgentHost.ZERO.value,
            agent_id="zero",
            start_time=0,
        )

    first = events[0]
    summary = RunSummary(
        run_id=first.run_id,
        session_id=first.session_id,
        host=AgentHost.ZERO.value,
        agent_id=first.agent_id,
        start_time=first.timestamp,
        project_key=detect_project_key(first.metadata.get("cwd")),
    )

    for ev in events:
        if ev.timestamp > summary.start_time:
            summary.end_time = ev.timestamp

        if ev.type == EventType.READ_FILE:
            summary.read_count += 1
            if ev.file_path:
                summary.files_read.add(ev.file_path)

        elif ev.type in (EventType.WRITE_FILE, EventType.EDIT_FILE):
            summary.write_count += 1 if ev.type == EventType.WRITE_FILE else 0
            summary.edit_count += 1 if ev.type == EventType.EDIT_FILE else 0
            if ev.file_path:
                summary.files_written.add(ev.file_path)

        elif ev.type == EventType.BASH:
            summary.bash_count += 1
            if ev.command:
                summary.commands_run.append(ev.command[:80])

        elif ev.type == EventType.TOOL_CALL:
            summary.tool_call_count += 1

        elif ev.type == EventType.SUBAGENT_SPAWN:
            summary.subagent_count += 1
            if ev.subagent_role:
                summary.subagent_roles.append(ev.subagent_role)

        elif ev.type == EventType.ERROR:
            summary.error_count += 1

        elif ev.type == EventType.COMPACT:
            summary.compact_count += 1

        elif ev.type == EventType.USAGE:
            summary.total_prompt_tokens += ev.prompt_tokens or 0
            summary.total_completion_tokens += ev.completion_tokens or 0
            summary.total_tokens += ev.total_tokens or 0

        # Track context pressure
        if ev.context_tokens and ev.context_tokens > summary.peak_context_tokens:
            summary.peak_context_tokens = ev.context_tokens

    return summary


class ZeroAdapter:
    """Adapter for Zero agent telemetry.

    Two modes:
      1. watch_stream — pipes a running `zero exec` process
      2. ingest_jsonl — parses pre-recorded stream-JSON files
    """

    def __init__(self, on_event: Callable[[TraceEvent], None] | None = None):
        self._on_event = on_event
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def ingest_jsonl(self, path: str | Path, run_id: str = "") -> list[TraceEvent]:
        """Parse a Zero stream-JSON JSONL file into canonical events."""
        parsed: list[TraceEvent] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = parse_zero_event(raw, run_id)
                if ev is not None:
                    parsed.append(ev)
                    self._events.append(ev)
        return parsed

    def watch_stream(
        self,
        command: list[str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Start watching a Zero exec stream-JSON process.

        Launches `zero exec ... --output-format stream-json` and parses
        every JSON line.
        """
        if self._running:
            return

        cmd = command or [
            "zero",
            "exec",
            "--output-format",
            "stream-json",
        ]

        self._running = True
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

        self._thread = threading.Thread(
            target=self._read_stream,
            daemon=True,
        )
        self._thread.start()

    def _read_stream(self) -> None:
        run_id = ""
        for line in self._process.stdout or []:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not run_id and raw.get("runId"):
                run_id = raw["runId"]

            ev = parse_zero_event(raw, run_id)
            if ev is not None:
                self._events.append(ev)
                if self._on_event:
                    self._on_event(ev)

    def stop(self) -> None:
        """Stop the stream watcher."""
        self._running = False
        if self._process:
            self._process.terminate()
            self._process = None

    def get_summary(self) -> RunSummary:
        return build_summary_from_zero_events(self._events)


# ── Zero plugin template ──────────────────────────────────────────────────

ZERO_TELEMETRY_PLUGIN = {
    "name": "eling-blackbox",
    "description": "Pipes Zero telemetry to eling-blackbox Layer 2 for flight recording and context-efficiency scoring",
    "hooks": {
        "sessionStart": {
            "command": "python3",
            "args": [
                "-m",
                "eling.blackbox.adapters.zero_plugin",
                "session-start",
            ],
        },
        "sessionEnd": {
            "command": "python3",
            "args": [
                "-m",
                "eling.blackbox.adapters.zero_plugin",
                "session-end",
            ],
        },
        "afterTool": {
            "command": "python3",
            "args": [
                "-m",
                "eling.blackbox.adapters.zero_plugin",
                "after-tool",
            ],
        },
    },
}


def install_zero_plugin(target_dir: str | Path | None = None) -> str:
    """Install the eling-blackbox telemetry plugin for Zero.

    Writes a plugin.json to Zero's user plugin directory
    (~/.config/zero/plugins/eling-blackbox/plugin.json).
    """
    if target_dir is None:
        target_dir = Path.home() / ".config" / "zero" / "plugins" / "eling-blackbox"
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    plugin_path = target_dir / "plugin.json"
    with open(plugin_path, "w") as f:
        json.dump(ZERO_TELEMETRY_PLUGIN, f, indent=2)

    return str(plugin_path)

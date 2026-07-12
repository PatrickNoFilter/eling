"""Hermes telemetry adapter — taps into Hermes session DB and plugin hooks.

Hermes persists conversations in SQLite and supports MCP-connected tools.
This adapter reads past sessions from the DB and can hook into live Hermes
runs via the Hermes plugin system or session_search API.
"""

from __future__ import annotations

import json
import os
import sqlite3
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


# Hermes session DB schema paths
HERMES_DB_CANDIDATES = [
    Path.home() / ".hermes" / "sessions.db",
    Path.home() / ".hermes" / "data" / "sessions.db",
    Path.home() / ".local" / "share" / "hermes" / "sessions.db",
]


def _find_hermes_db() -> str | None:
    for p in HERMES_DB_CANDIDATES:
        if p.exists():
            return str(p)
    # Environment override
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        p = Path(env_home) / "sessions.db"
        if p.exists():
            return str(p)
    return None


class HermesAdapter:
    """Adapter for Hermes agent telemetry.

    Reads Hermes session DB and provides live observation hooks.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        on_event: Callable[[TraceEvent], None] | None = None,
    ):
        self._db_path = str(db_path) if db_path else (_find_hermes_db() or "")
        self._on_event = on_event
        self._events: list[TraceEvent] = []

    @property
    def available(self) -> bool:
        return bool(self._db_path) and Path(self._db_path).exists()

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent Hermes sessions from the session DB."""
        if not self.available:
            return []

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT id, title, created_at, updated_at, message_count
                   FROM sessions ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_session_messages(
        self,
        session_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get messages from a Hermes session."""
        if not self.available:
            return []

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Try the messages table (Hermes schema)
            try:
                rows = conn.execute(
                    """SELECT id, role, content, tool_calls, created_at
                       FROM messages WHERE session_id = ?
                       ORDER BY id ASC LIMIT ?""",
                    (session_id, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                # Fallback: try conversation table
                try:
                    rows = conn.execute(
                        """SELECT id, role, content, created_at
                           FROM conversation WHERE session_id = ?
                           ORDER BY id ASC LIMIT ?""",
                        (session_id, limit),
                    ).fetchall()
                    return [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    return []
        finally:
            conn.close()

    def ingest_session(self, session_id: int, run_id: str = "") -> list[TraceEvent]:
        """Convert a Hermes session into canonical telemetry events."""
        messages = self.get_session_messages(session_id)
        events: list[TraceEvent] = []

        timestamp = time.time()
        base_run_id = run_id or f"hermes_{session_id}_{int(timestamp)}"

        # Session start
        events.append(
            TraceEvent(
                type=EventType.SESSION_START,
                timestamp=timestamp,
                agent_id="hermes",
                host=AgentHost.HERMES.value,
                run_id=base_run_id,
                session_id=str(session_id),
            )
        )

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls")
            created_at = msg.get("created_at", timestamp)
            if isinstance(created_at, str):
                try:
                    from datetime import datetime

                    created_at = datetime.fromisoformat(created_at).timestamp()
                except Exception:
                    created_at = timestamp

            if role == "assistant" and tool_calls:
                # Parse tool calls from Hermes format
                try:
                    calls = (
                        json.loads(tool_calls)
                        if isinstance(tool_calls, str)
                        else tool_calls
                    )
                except (json.JSONDecodeError, TypeError):
                    calls = []

                for tc in calls or []:
                    tc_name = tc.get("name", tc.get("function", {}).get("name", ""))
                    tc_args = tc.get(
                        "arguments", tc.get("function", {}).get("arguments", {})
                    )
                    if isinstance(tc_args, str):
                        try:
                            tc_args = json.loads(tc_args)
                        except json.JSONDecodeError:
                            tc_args = {}

                    ev = TraceEvent(
                        type=EventType.TOOL_CALL,
                        timestamp=created_at,
                        agent_id="hermes",
                        host=AgentHost.HERMES.value,
                        run_id=base_run_id,
                        session_id=str(session_id),
                        tool_name=tc_name,
                        tool_args=tc_args,
                    )

                    # Map known tools
                    if tc_name in ("read_file",):
                        ev.type = EventType.READ_FILE
                        ev.file_path = tc_args.get("path", "")
                    elif tc_name in ("write_file", "create_file"):
                        ev.type = EventType.WRITE_FILE
                        ev.file_path = tc_args.get("path", "")
                    elif tc_name in ("patch", "edit_file"):
                        ev.type = EventType.EDIT_FILE
                        ev.file_path = tc_args.get("path", "")
                    elif tc_name in ("terminal", "bash"):
                        ev.type = EventType.BASH
                        ev.command = tc_args.get("command", "")
                    elif tc_name == "delegate_task":
                        ev.type = EventType.SUBAGENT_SPAWN
                        ev.subagent_role = tc_args.get("role", tc_args.get("name", ""))
                        ev.subagent_prompt = tc_args.get("goal", "")

                    events.append(ev)

            elif role == "tool":
                # Tool result
                ev = TraceEvent(
                    type=EventType.TOOL_RESULT,
                    timestamp=created_at,
                    agent_id="hermes",
                    host=AgentHost.HERMES.value,
                    run_id=base_run_id,
                    session_id=str(session_id),
                    tool_output=content[:500],
                    output_size=len(content),
                )
                events.append(ev)

        # Session end
        events.append(
            TraceEvent(
                type=EventType.SESSION_END,
                timestamp=time.time(),
                agent_id="hermes",
                host=AgentHost.HERMES.value,
                run_id=base_run_id,
                session_id=str(session_id),
            )
        )

        self._events.extend(events)
        return events

    def build_summary(self, events: list[TraceEvent] | None = None) -> RunSummary:
        """Build a RunSummary from Hermes events."""
        evs = events or self._events
        if not evs:
            return RunSummary(
                run_id="",
                session_id="",
                host=AgentHost.HERMES.value,
                agent_id="hermes",
                start_time=time.time(),
            )

        first = evs[0]
        summary = RunSummary(
            run_id=first.run_id,
            session_id=first.session_id,
            host=AgentHost.HERMES.value,
            agent_id=first.agent_id,
            start_time=first.timestamp,
            project_key=detect_project_key(),
        )

        for ev in evs:
            summary.end_time = max(summary.end_time or 0, ev.timestamp)

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

            elif ev.type == EventType.USAGE:
                summary.total_prompt_tokens += ev.prompt_tokens or 0
                summary.total_completion_tokens += ev.completion_tokens or 0
                summary.total_tokens += ev.total_tokens or 0

        return summary

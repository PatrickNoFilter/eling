"""Blackbox telemetry store — SQLite-backed event log with eling interop.

Stores raw events for replay and aggregates run summaries.
Feeds findings into Facts (Layer 3) via eling.brain.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .core import (
    TraceEvent,
    RunSummary,
    EfficiencyReport,
    EffectivenessReport,
    TaskArchetype,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    host TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    project_key TEXT DEFAULT '',
    archetype TEXT DEFAULT 'unknown',
    start_time REAL NOT NULL,
    end_time REAL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    type TEXT NOT NULL,
    event_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_key);
CREATE INDEX IF NOT EXISTS idx_runs_host ON runs(host);

CREATE TABLE IF NOT EXISTS efficiency_reports (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    report_json TEXT NOT NULL,
    overall_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS effectiveness_reports (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    report_json TEXT NOT NULL,
    score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS baselines (
    project_key TEXT NOT NULL,
    archetype TEXT NOT NULL,
    baseline_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (project_key, archetype)
);
"""


class BlackboxStore:
    """Thread-safe telemetry store backed by SQLite.

    Each run is a session of continuous agent activity. Events are
    appended sequentially for replay. Findings can be pushed to
    eling's facts layer via the provided brain reference.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or Path.home() / ".eling" / "blackbox.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._brain = None  # optional eling.brain.Brain reference
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def set_brain(self, brain: Any) -> None:
        """Wire an optional eling Brain for auto-pushing findings to Facts layer."""
        self._brain = brain

    # ── Event ingestion ──────────────────────────────────────────────────

    def ingest(self, event: TraceEvent) -> None:
        """Store a single telemetry event."""
        seq = None
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                (event.run_id,),
            )
            seq = cur.fetchone()[0]

            # Upsert run
            self._conn.execute(
                """INSERT OR IGNORE INTO runs
                   (run_id, session_id, host, agent_id, start_time)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.run_id,
                    event.session_id,
                    event.host,
                    event.agent_id,
                    event.timestamp,
                ),
            )

            self._conn.execute(
                "INSERT INTO events (run_id, seq, timestamp, type, event_json) VALUES (?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    seq,
                    event.timestamp,
                    event.type.value,
                    json.dumps(event.to_dict()),
                ),
            )
            self._conn.commit()

    def ingest_batch(self, events: list[TraceEvent]) -> int:
        """Batch ingest multiple events. Returns count."""
        with self._lock:
            for ev in events:
                cur = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                    (ev.run_id,),
                )
                seq = cur.fetchone()[0]
                self._conn.execute(
                    "INSERT OR IGNORE INTO runs (run_id, session_id, host, agent_id, start_time) VALUES (?, ?, ?, ?, ?)",
                    (ev.run_id, ev.session_id, ev.host, ev.agent_id, ev.timestamp),
                )
                self._conn.execute(
                    "INSERT INTO events (run_id, seq, timestamp, type, event_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        ev.run_id,
                        seq,
                        ev.timestamp,
                        ev.type.value,
                        json.dumps(ev.to_dict()),
                    ),
                )
            self._conn.commit()
        return len(events)

    def finalize_run(self, run_id: str, summary: RunSummary) -> None:
        """Mark a run as complete with its summary."""
        with self._lock:
            self._conn.execute(
                """UPDATE runs SET end_time = ?, project_key = ?, archetype = ?,
                   summary_json = ? WHERE run_id = ?""",
                (
                    summary.end_time or time.time(),
                    summary.project_key,
                    summary.archetype.value,
                    json.dumps(summary.to_dict()),
                    run_id,
                ),
            )
            self._conn.commit()

    # ── Query ────────────────────────────────────────────────────────────

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_runs(
        self,
        host: str | None = None,
        project_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if host:
            q += " AND host = ?"
            params.append(host)
        if project_key:
            q += " AND project_key = ?"
            params.append(project_key)
        q += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def get_events(
        self,
        run_id: str,
        seq_from: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_json FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, seq_from, limit),
            ).fetchall()
        return [json.loads(r["event_json"]) for r in rows]

    def get_efficiency(self, run_id: str) -> EfficiencyReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT report_json FROM efficiency_reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["report_json"])
        # Reconstruct TaskArchetype from string
        if isinstance(data.get("archetype"), str):
            data["archetype"] = TaskArchetype(data["archetype"])
        return EfficiencyReport(**data)

    def save_efficiency(self, run_id: str, report: EfficiencyReport) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO efficiency_reports
                   (run_id, report_json, overall_score) VALUES (?, ?, ?)""",
                (run_id, json.dumps(report.to_dict()), report.overall_score),
            )
            self._conn.commit()

    def get_effectiveness(self, run_id: str) -> EffectivenessReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT report_json FROM effectiveness_reports WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["report_json"])
        return EffectivenessReport(**data)

    def save_effectiveness(self, run_id: str, report: EffectivenessReport) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO effectiveness_reports
                   (run_id, report_json, score) VALUES (?, ?, ?)""",
                (run_id, json.dumps(report.to_dict()), report.score),
            )
            self._conn.commit()

    def get_baselines(self, project_key: str, archetype: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT baseline_json FROM baselines WHERE project_key = ? AND archetype = ?",
                (project_key, archetype),
            ).fetchone()
        return json.loads(row["baseline_json"]) if row else None

    def save_baselines(
        self, project_key: str, archetype: str, data: dict[str, Any]
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO baselines
                   (project_key, archetype, baseline_json, updated_at) VALUES (?, ?, ?, ?)""",
                (project_key, archetype, json.dumps(data), time.time()),
            )
            self._conn.commit()

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
            self._conn.execute(
                "DELETE FROM effectiveness_reports WHERE run_id = ?", (run_id,)
            )
            self._conn.execute(
                "DELETE FROM efficiency_reports WHERE run_id = ?", (run_id,)
            )
            self._conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

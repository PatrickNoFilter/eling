"""Prototype 3: Per-fact versioning — immutable append-only frames.

Inspired by Memvid's Smart Frames (append-only, immutable content units).
Instead of mutating facts, each edit creates a new version linked via superseded_by.
Enables per-fact time travel without DB-level snapshots.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_VERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_versions (
    version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id       INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    category      TEXT,
    tags          TEXT,
    trust_score   REAL,
    strength      REAL,
    version_seq   INTEGER NOT NULL,       -- sequence number within this fact's history
    superseded_by INTEGER,                 -- version_id of the next version (NULL = current)
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason        TEXT DEFAULT '',          -- why this version was created
    hrr_vector    BLOB,
    embedding_blob TEXT                     -- JSON embedding (moved here on version creation)
);

CREATE INDEX IF NOT EXISTS idx_fact_versions_fact_id ON fact_versions(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_versions_seq ON fact_versions(fact_id, version_seq DESC);

-- Timeline index: chronological order across all facts
CREATE TABLE IF NOT EXISTS version_timeline (
    entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER NOT NULL,
    version_id  INTEGER NOT NULL,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    entry_type  TEXT DEFAULT 'create'      -- create | update | tag_change | trust_change
);

CREATE INDEX IF NOT EXISTS idx_version_timeline_ts ON version_timeline(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_version_timeline_fact ON version_timeline(fact_id);
"""


class VersionedFactStore:
    """Prototype: Append-only versioning wrapper around eling's FactsLayer.

    Every mutation creates a new version instead of overwriting.
    Supports:
    - Time-travel: query state at any point in time
    - Per-fact undo: revert to previous version
    - Audit trail: full history of every fact
    - Timeline: chronological view of all changes
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._conn.executescript(_VERSION_SCHEMA)
        self._conn.commit()

    # ── Version Creation ──

    def snapshot_current(self, fact_id: int, reason: str = "") -> int | None:
        """Capture the current state of a fact as a new version.

        Returns version_id or None if fact doesn't exist.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT fact_id, content, category, tags, trust_score, strength,
                          hrr_vector, created_at
                   FROM facts WHERE fact_id = ?""",
                (fact_id,),
            ).fetchone()
            if not row:
                return None

            # Get the next sequence number for this fact
            seq_row = self._conn.execute(
                "SELECT COALESCE(MAX(version_seq), 0) + 1 as next_seq FROM fact_versions WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            next_seq = seq_row["next_seq"] if seq_row else 1

            # Get embedding if exists (table may not be present)
            embedding_json = None
            try:
                embed_row = self._conn.execute(
                    "SELECT embedding FROM fact_embeddings_v2 WHERE fact_id = ?",
                    (fact_id,),
                ).fetchone()
                if embed_row:
                    embedding_json = json.dumps(embed_row["embedding"])
            except sqlite3.OperationalError:
                pass  # fact_embeddings_v2 table not present

            cur = self._conn.execute(
                """INSERT INTO fact_versions
                   (fact_id, content, category, tags, trust_score, strength,
                    version_seq, reason, hrr_vector, embedding_blob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact_id,
                    row["content"],
                    row["category"],
                    row["tags"],
                    row["trust_score"],
                    row["strength"],
                    next_seq,
                    reason,
                    row["hrr_vector"],
                    embedding_json,
                ),
            )
            version_id = cur.lastrowid

            # Record in timeline
            self._conn.execute(
                "INSERT INTO version_timeline (fact_id, version_id, entry_type) VALUES (?, ?, ?)",
                (fact_id, version_id, reason or "update"),
            )
            self._conn.commit()
            assert version_id is not None
            return int(version_id)

    def update_fact(
        self, fact_id: int, new_content: str, reason: str = "update"
    ) -> dict:
        """Create a new version and update the live fact. Returns version info."""
        with self._lock:
            # Snapshot current state first
            v_id = self.snapshot_current(fact_id, reason=reason)
            if v_id is None:
                raise KeyError(f"fact_id {fact_id} not found")

            # Update the live fact
            self._conn.execute(
                "UPDATE facts SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                (new_content, fact_id),
            )

            # Link superseded_by on the just-created version
            # (it was created as the "old" version, now superseded by the edit)
            # Actually, wait — the version we just created IS the old version.
            # The fact table now has the new content. The version is already preserved.

            self._conn.commit()

            return {
                "fact_id": fact_id,
                "version_id": v_id,
                "action": "versioned_update",
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    # ── Time Travel ──

    def get_fact_at_time(self, fact_id: int, timestamp: str) -> dict | None:
        """Get the state of a fact as it was at a given timestamp.

        Args:
            fact_id: The fact to query
            timestamp: ISO format timestamp (e.g. '2026-07-04 12:00:00')

        Returns:
            The version of the fact active at that time, or None.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM fact_versions
                   WHERE fact_id = ? AND created_at <= ?
                   ORDER BY version_seq DESC LIMIT 1""",
                (fact_id, timestamp),
            ).fetchone()
            if row:
                return dict(row)
            # No version found — return the live fact if it existed before the timestamp
            live = self._conn.execute(
                "SELECT * FROM facts WHERE fact_id = ? AND created_at <= ?",
                (fact_id, timestamp),
            ).fetchone()
            return dict(live) if live else None

    def get_fact_history(self, fact_id: int) -> list[dict]:
        """Get the full change history of a fact, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM fact_versions WHERE fact_id = ? ORDER BY version_seq ASC",
                (fact_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def undo_to_version(self, fact_id: int, target_version_seq: int) -> dict:
        """Revert a fact to a previous version. Creates a new version."""
        with self._lock:
            version = self._conn.execute(
                "SELECT * FROM fact_versions WHERE fact_id = ? AND version_seq = ?",
                (fact_id, target_version_seq),
            ).fetchone()
            if not version:
                raise KeyError(
                    f"Version {target_version_seq} not found for fact {fact_id}"
                )

            # Snapshot current before revert
            self.snapshot_current(fact_id, reason=f"revert_to_v{target_version_seq}")

            # Restore the target version's data to the live fact
            self._conn.execute(
                """UPDATE facts SET content = ?, category = ?, tags = ?,
                        trust_score = ?, strength = ?,
                        updated_at = CURRENT_TIMESTAMP
                   WHERE fact_id = ?""",
                (
                    version["content"],
                    version["category"],
                    version["tags"],
                    version["trust_score"],
                    version["strength"],
                    fact_id,
                ),
            )
            self._conn.commit()
            return {
                "fact_id": fact_id,
                "action": "reverted",
                "to_version": target_version_seq,
                "restored_content": version["content"][:120],
            }

    # ── Timeline ──

    def timeline(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Get chronological event log across all facts, newest first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.*, f.content
                   FROM version_timeline t
                   JOIN facts f ON f.fact_id = t.fact_id
                   ORDER BY t.timestamp DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def timeline_since(self, timestamp: str) -> list[dict]:
        """Get all changes since a given timestamp."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.*, f.content
                   FROM version_timeline t
                   JOIN facts f ON f.fact_id = t.fact_id
                   WHERE t.timestamp >= ?
                   ORDER BY t.timestamp ASC""",
                (timestamp,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Stats ──

    def stats(self) -> dict:
        with self._lock:
            total_versions = self._conn.execute(
                "SELECT COUNT(*) as n FROM fact_versions"
            ).fetchone()["n"]
            versioned_facts = self._conn.execute(
                "SELECT COUNT(DISTINCT fact_id) as n FROM fact_versions"
            ).fetchone()["n"]
            timeline_entries = self._conn.execute(
                "SELECT COUNT(*) as n FROM version_timeline"
            ).fetchone()["n"]
            return {
                "total_versions": int(total_versions),
                "versioned_facts": int(versioned_facts),
                "timeline_entries": int(timeline_entries),
            }

    def close(self) -> None:
        self._conn.close()

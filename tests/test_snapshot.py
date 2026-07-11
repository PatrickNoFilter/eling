"""Tests for snapshot / rollback (Task 13.1)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from eling.snapshot import (
    SNAPSHOT_KEEP,
    create_snapshot,
    list_snapshots,
    rollback,
)


@pytest.fixture
def facts_db():
    """Create a temporary facts.db with a couple of facts."""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "facts.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE facts (
                fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT 'general',
                tags TEXT DEFAULT '',
                trust_score REAL DEFAULT 0.5,
                source TEXT DEFAULT 'facts',
                strength REAL DEFAULT 1.0,
                last_access_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO facts (content, category) VALUES ('Einstein was a physicist', 'fact');
            INSERT INTO facts (content, category) VALUES ('Mars is red', 'fact');
        """)
        conn.commit()
        conn.close()
        yield db


class TestCreateSnapshot:
    def test_creates_db_and_json(self, facts_db):
        meta = create_snapshot(facts_db, reason="pre_decay")
        assert meta["snapshot_id"]
        assert meta["reason"] == "pre_decay"
        assert meta["fact_count"] == 2

        snap_dir = facts_db.parent / "snapshots"
        snap_db = snap_dir / f"{meta['snapshot_id']}.db"
        snap_meta = snap_dir / f"{meta['snapshot_id']}.json"
        assert snap_db.is_file()
        assert snap_meta.is_file()

    def test_fact_count_zero_empty_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE facts (fact_id INTEGER PRIMARY KEY, content TEXT)"
            )
            conn.commit()
            conn.close()
            meta = create_snapshot(db)
            assert meta["fact_count"] == 0

    def test_missing_db_raises(self):
        with pytest.raises(FileNotFoundError):
            create_snapshot("/nonexistent/db.db")

    def test_default_reason_empty(self, facts_db):
        meta = create_snapshot(facts_db)
        assert meta["reason"] == ""


class TestListSnapshots:
    def test_empty_when_no_snapshots(self, facts_db):
        snaps = list_snapshots(facts_db)
        assert snaps == []

    def test_lists_one_snapshot(self, facts_db):
        create_snapshot(facts_db, reason="test")
        snaps = list_snapshots(facts_db)
        assert len(snaps) == 1
        assert snaps[0]["reason"] == "test"

    def test_newest_first(self, facts_db):
        m1 = create_snapshot(facts_db, reason="first")
        m2 = create_snapshot(facts_db, reason="second")
        snaps = list_snapshots(facts_db)
        assert snaps[0]["snapshot_id"] == m2["snapshot_id"]
        assert snaps[1]["snapshot_id"] == m1["snapshot_id"]

    def test_invalid_json_skipped(self, facts_db):
        create_snapshot(facts_db, reason="good")
        # Orphan the JSON
        snap_dir = facts_db.parent / "snapshots"
        bad = snap_dir / "bad-snap.json"
        bad.write_text("not json")
        snaps = list_snapshots(facts_db)
        assert len(snaps) == 1


class TestRollback:
    def test_rollback_restores_content(self, facts_db):
        # Snapshot with 2 facts
        meta = create_snapshot(facts_db, reason="baseline")

        # Add a fact (simulate decay damage)
        conn = sqlite3.connect(str(facts_db))
        conn.execute("INSERT INTO facts (content) VALUES ('should be removed')")
        conn.commit()
        conn.close()

        # Verify 3 facts
        conn = sqlite3.connect(str(facts_db))
        n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert n == 3
        conn.close()

        # Rollback
        rb = rollback(meta["snapshot_id"], facts_db)
        assert rb["snapshot_id"] == meta["snapshot_id"]
        assert rb["current_backup"]

        # Verify restored to 2 facts
        conn = sqlite3.connect(str(facts_db))
        n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert n == 2
        conn.close()

    def test_rollback_unknown_id_raises(self, facts_db):
        with pytest.raises(FileNotFoundError):
            rollback("nonexistent", facts_db)

    def test_rollback_includes_backup_snapshot(self, facts_db):
        meta = create_snapshot(facts_db, reason="baseline")
        conn = sqlite3.connect(str(facts_db))
        conn.execute("INSERT INTO facts (content) VALUES ('extra')")
        conn.commit()
        conn.close()

        rb = rollback(meta["snapshot_id"], facts_db)
        # A pre-rollback backup should exist
        snaps = list_snapshots(facts_db)
        backup_ids = [s["snapshot_id"] for s in snaps]
        assert rb["current_backup"] in backup_ids


class TestPruning:
    def test_keeps_only_n_snapshots(self, facts_db):
        """After creating 7 snapshots, only the most recent 5 remain."""
        for i in range(7):
            create_snapshot(facts_db, reason=f"snap-{i}")
        snaps = list_snapshots(facts_db)
        assert len(snaps) == SNAPSHOT_KEEP

    def test_oldest_removed(self, facts_db):
        for i in range(7):
            create_snapshot(facts_db, reason=f"snap-{i}")
        snaps = list_snapshots(facts_db)
        reasons = [s["reason"] for s in snaps]
        assert "snap-0" not in reasons
        assert "snap-6" in reasons  # most recent

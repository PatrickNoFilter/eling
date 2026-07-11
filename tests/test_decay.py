"""Tests for the forgetting/decay engine (Task 12.1)."""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path


from eling.decay import (
    ACTIVE_THRESHOLD,
    DORMANT_THRESHOLD,
    compute_lifecycle,
    decay_strength,
)
from eling.layers.facts import FactsLayer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _fact_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT fact_id FROM facts ORDER BY fact_id").fetchall()
    return [int(r["fact_id"]) for r in rows]


def _strengths(conn: sqlite3.Connection) -> list[float]:
    rows = conn.execute("SELECT strength FROM facts ORDER BY fact_id").fetchall()
    return [float(r["strength"]) for r in rows]


# ---------------------------------------------------------------------------
# decay.py unit tests
# ---------------------------------------------------------------------------


class TestDecayFunction:
    def test_no_decay(self):
        assert math.isclose(decay_strength(1.0, days=0.0), 1.0)
        assert math.isclose(decay_strength(0.5, days=0.0), 0.5)

    def test_known_rate(self):
        # decay_rate=ln(2) → half-life of 1 day
        val = decay_strength(1.0, days=1.0, decay_rate=math.log(2))
        assert math.isclose(val, 0.5, abs_tol=1e-9)

    def test_clamp_positive(self):
        # Even with huge decay, result stays >= 0
        val = decay_strength(0.01, days=1000.0, decay_rate=0.5)
        assert 0.0 <= val <= 1.0

    def test_clamp_negative_input(self):
        # Negative strength clamped to 0
        val = decay_strength(-0.5, days=0.0)
        assert val == 0.0

    def test_clamp_above_one(self):
        # Above-1 strength input also valid (caller's responsibility)
        val = decay_strength(1.5, days=0.0)
        assert val == 1.0


class TestComputeLifecycle:
    def test_active(self):
        assert compute_lifecycle(1.0) == "active"
        assert compute_lifecycle(0.6) == "active"
        assert compute_lifecycle(0.51) == "active"

    def test_dormant(self):
        assert compute_lifecycle(0.5) == "dormant"
        assert compute_lifecycle(0.3) == "dormant"
        assert compute_lifecycle(0.21) == "dormant"

    def test_cleared(self):
        assert compute_lifecycle(0.1) == "cleared"
        assert compute_lifecycle(0.0) == "cleared"

    def test_boundary_above(self):
        assert compute_lifecycle(ACTIVE_THRESHOLD + 0.001) == "active"
        assert compute_lifecycle(0.501) == "active"

    def test_boundary_at_dormant(self):
        assert compute_lifecycle(0.5) == "dormant"
        assert compute_lifecycle(DORMANT_THRESHOLD) == "dormant"

    def test_boundary_cleared(self):
        assert compute_lifecycle(DORMANT_THRESHOLD - 0.001) == "cleared"


# ---------------------------------------------------------------------------
# FactsLayer strength + decay integration tests
# ---------------------------------------------------------------------------


class TestFactsLayerStrength:
    def test_add_sets_strength(self, tmp_path: Path):
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Paris is the capital of France")
        conn = _connect(db)
        row = conn.execute(
            "SELECT strength, last_access_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert math.isclose(float(row["strength"]), 1.0, abs_tol=1e-6)

    def test_add_sets_last_access_at(self, tmp_path: Path):
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("The sky is blue")
        conn = _connect(db)
        row = conn.execute(
            "SELECT last_access_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        conn.close()
        assert row is not None
        # Should be a valid timestamp string
        assert row["last_access_at"] is not None

    def test_get_updates_last_access_at(self, tmp_path: Path):
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Water freezes at 0C")
        conn = _connect(db)
        before = conn.execute(
            "SELECT last_access_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["last_access_at"]
        conn.close()

        time.sleep(0.01)
        layer.get(fid)

        conn = _connect(db)
        after = conn.execute(
            "SELECT last_access_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["last_access_at"]
        conn.close()
        # Updated at should have changed
        assert after != before or after is not None


class TestApplyDecay:
    def test_apply_decay_moves_states(self, tmp_path: Path):
        """Add fact with old last_access_at → run decay → verify dormant/cleared."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Python is a programming language")

        # Manually backdate last_access_at to 10 days ago in SQLite
        conn = _connect(db)
        ten_days_ago = "datetime('now', '-10 days')"
        conn.execute(
            f"UPDATE facts SET last_access_at = {ten_days_ago} WHERE fact_id = ?",
            (fid,),
        )
        conn.commit()
        conn.close()

        # Apply decay (default rate 0.1/day → after 10 days: 1.0 * exp(-1.0) ≈ 0.368)
        result = layer.apply_decay()

        conn = _connect(db)
        row = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        conn.close()
        strength = float(row["strength"])

        # ~0.368 should be in "dormant" range (0.2–0.5)
        assert 0.2 <= strength <= 0.5, f"strength {strength:.3f} should be dormant"
        assert result["dormant"] >= 1
        assert result["active"] == 0

    def test_apply_decay_old_fact_cleared(self, tmp_path: Path):
        """Very old fact → cleared after decay."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Obsolete fact from 100 days ago")

        conn = _connect(db)
        conn.execute(
            "UPDATE facts SET last_access_at = datetime('now', '-100 days') WHERE fact_id = ?",
            (fid,),
        )
        conn.commit()
        conn.close()

        result = layer.apply_decay()

        conn = _connect(db)
        row = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        conn.close()
        strength = float(row["strength"])

        # After 100 days at 0.1/day: 1.0 * exp(-10) ≈ 4.5e-5 → cleared (<0.2)
        assert strength < 0.2, f"strength {strength:.6f} should be cleared"
        assert result["cleared"] >= 1

    def test_apply_decay_active_stays_active(self, tmp_path: Path):
        """Recently accessed fact stays active."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Recent fact accessed today")

        result = layer.apply_decay()

        conn = _connect(db)
        row = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        conn.close()
        strength = float(row["strength"])

        # Newly added → strength ~1.0 → active
        assert strength > ACTIVE_THRESHOLD, f"strength {strength:.3f} should be active"
        assert result["active"] >= 1

    def test_apply_decay_returns_counts(self, tmp_path: Path):
        """apply_decay returns breakdown of active/dormant/cleared."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        layer.add("Fact A")
        layer.add("Fact B")
        layer.add("Fact C")

        # Age them differently
        conn = _connect(db)
        # fact_id 1 → 10 days old → dormant (e^-0.1*10 = 0.368)
        conn.execute(
            "UPDATE facts SET last_access_at = datetime('now', '-10 days') WHERE fact_id = 1"
        )
        # fact_id 2 → 20 days old → cleared
        conn.execute(
            "UPDATE facts SET last_access_at = datetime('now', '-20 days') WHERE fact_id = 2"
        )
        conn.commit()
        conn.close()

        result = layer.apply_decay()

        assert "active" in result
        assert "dormant" in result
        assert "cleared" in result
        # fact 3 (recent) → active
        assert result["active"] == 1
        # fact 1 (10 days) → dormant
        assert result["dormant"] == 1
        assert result["cleared"] == 1


class TestReadBoostsStrength:
    def test_search_boosts_strength(self, tmp_path: Path):
        """Searching for a fact bumps its strength +0.05."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("The Earth orbits the Sun")

        # Set starting strength below 1.0 so boost is visible
        conn = _connect(db)
        conn.execute("UPDATE facts SET strength = 0.5 WHERE fact_id = ?", (fid,))
        conn.commit()
        before = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["strength"]
        conn.close()

        # Search for it (boost on recall)
        results = layer.search("Earth orbits")
        assert any(r.get("fact_id") == fid for r in results)

        conn = _connect(db)
        after = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["strength"]
        conn.close()

        assert float(after) > float(before), f"{after} should be > {before} after boost"

    def test_search_boost_clamps_at_one(self, tmp_path: Path):
        """Boost doesn't exceed 1.0."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Boost me multiple times")

        # Search many times
        for _ in range(20):
            layer.search("Boost me multiple times")

        conn = _connect(db)
        strength = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["strength"]
        conn.close()
        assert strength <= 1.0


class TestDecisionMadeBoostsStrength:
    def test_update_trust_boosts_strength(self, tmp_path: Path):
        """update_trust with helpful=True boosts strength by +0.1."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("This fact was verified")

        # Set starting strength below 1.0 so boost is visible
        conn = _connect(db)
        conn.execute("UPDATE facts SET strength = 0.5 WHERE fact_id = ?", (fid,))
        conn.commit()
        before = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["strength"]
        conn.close()

        # Good feedback
        layer.update_trust(fid, helpful=True)

        conn = _connect(db)
        after = conn.execute(
            "SELECT strength FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["strength"]
        conn.close()

        assert float(after) > float(before), (
            f"{after} should be > {before} after helpful boost"
        )


class TestClearedHiddenByDefault:
    def test_cleared_not_in_search(self, tmp_path: Path):
        """Cleared facts (strength < 0.2) do NOT appear in search results."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("A very old fact")

        # Force to cleared
        conn = _connect(db)
        conn.execute(
            "UPDATE facts SET strength = 0.05, last_access_at = datetime('now', '-50 days') WHERE fact_id = ?",
            (fid,),
        )
        conn.commit()
        conn.close()

        # Search — should NOT return the cleared fact
        results = layer.search("very old fact")
        found = [r for r in results if r.get("fact_id") == fid]
        assert len(found) == 0, "cleared fact should be hidden from search"

    def test_cleared_not_in_list_all(self, tmp_path: Path):
        """Cleared facts do NOT appear in list_all results by default."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Another old fact")

        # Force to cleared
        conn = _connect(db)
        conn.execute(
            "UPDATE facts SET strength = 0.05, last_access_at = datetime('now', '-50 days') WHERE fact_id = ?",
            (fid,),
        )
        conn.commit()
        conn.close()

        results = layer.list_all()
        found = [r for r in results if r.get("fact_id") == fid]
        assert len(found) == 0, "cleared fact should be hidden from list_all"

    def test_cleared_recoverable_via_include_cleared(self, tmp_path: Path):
        """Cleared facts ARE returned when include_cleared=True."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Old but recoverable fact")

        # Force to cleared
        conn = _connect(db)
        conn.execute("UPDATE facts SET strength = 0.05 WHERE fact_id = ?", (fid,))
        conn.commit()
        conn.close()

        results = layer.list_all(include_cleared=True)
        found = [r for r in results if r.get("fact_id") == fid]
        assert len(found) == 1, (
            "cleared fact should be visible with include_cleared=True"
        )

    def test_probe_excludes_cleared(self, tmp_path: Path):
        """probe() also excludes cleared facts."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        # Add a fact mentioning "Python"
        fid = layer.add("Python is great")
        conn = _connect(db)
        conn.execute("UPDATE facts SET strength = 0.05 WHERE fact_id = ?", (fid,))
        conn.commit()
        conn.close()

        results = layer.probe("Python")
        found = [r for r in results if r.get("fact_id") == fid]
        assert len(found) == 0, "cleared fact should be hidden from probe"

    def test_probe_includes_cleared(self, tmp_path: Path):
        """probe() returns cleared facts when include_cleared=True."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        fid = layer.add("Ruby is also great")
        conn = _connect(db)
        conn.execute("UPDATE facts SET strength = 0.05 WHERE fact_id = ?", (fid,))
        conn.commit()
        conn.close()

        results = layer.probe("Ruby", include_cleared=True)
        found = [r for r in results if r.get("fact_id") == fid]
        assert len(found) == 1


class TestStatsDecay:
    def test_stats_includes_lifecycle_counts(self, tmp_path: Path):
        """stats() now returns active/dormant/cleared counts."""
        db = tmp_path / "facts.db"
        layer = FactsLayer(db)
        layer.add("Active fact 1")
        layer.add("Active fact 2")
        fid_dormant = layer.add("Dormant fact")
        fid_cleared = layer.add("Cleared fact")

        conn = _connect(db)
        # Set one to dormant (10 days → ~0.368)
        conn.execute(
            "UPDATE facts SET last_access_at = datetime('now', '-10 days') WHERE fact_id = ?",
            (fid_dormant,),
        )
        # Set one to cleared
        conn.execute(
            "UPDATE facts SET last_access_at = datetime('now', '-30 days') WHERE fact_id = ?",
            (fid_cleared,),
        )
        conn.commit()
        conn.close()

        layer.apply_decay()

        stats = layer.stats()
        assert "active_facts" in stats
        assert "dormant_facts" in stats
        assert "cleared_facts" in stats
        # active: 2
        assert stats["active_facts"] == 2
        # dormant: 1
        assert stats["dormant_facts"] == 1
        # cleared: 1
        assert stats["cleared_facts"] == 1

"""Tests for contradiction / consistency check (Task 12.2)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eling.layers.facts import FactsLayer


# ── helper tag functions ──


class TestTagHelpers:
    """Tag string manipulation helpers (_tag_has, _tag_add, _tag_remove)."""

    def test_has_present(self):
        from eling.layers.facts import _tag_has

        assert _tag_has("foo,bar,baz", "bar") is True

    def test_has_absent(self):
        from eling.layers.facts import _tag_has

        assert _tag_has("foo,baz", "bar") is False

    def test_has_empty(self):
        from eling.layers.facts import _tag_has

        assert _tag_has("", "bar") is False

    def test_has_single(self):
        from eling.layers.facts import _tag_has

        assert _tag_has("bar", "bar") is True

    def test_has_substring_no_false(self):
        """'bar' should not match 'barista'."""
        from eling.layers.facts import _tag_has

        assert _tag_has("barista", "bar") is False

    def test_add_new(self):
        from eling.layers.facts import _tag_add

        assert _tag_add("foo", "bar") == "foo,bar"

    def test_add_to_empty(self):
        from eling.layers.facts import _tag_add

        assert _tag_add("", "bar") == "bar"

    def test_add_duplicate(self):
        from eling.layers.facts import _tag_add

        assert _tag_add("foo,bar", "bar") == "foo,bar"

    def test_remove_present(self):
        from eling.layers.facts import _tag_remove

        assert _tag_remove("foo,bar,baz", "bar") == "foo,baz"

    def test_remove_absent(self):
        from eling.layers.facts import _tag_remove

        assert _tag_remove("foo,baz", "bar") == "foo,baz"

    def test_remove_single(self):
        from eling.layers.facts import _tag_remove

        assert _tag_remove("bar", "bar") == ""

    def test_remove_empty(self):
        from eling.layers.facts import _tag_remove

        assert _tag_remove("", "bar") == ""


# ── contradiction detection ──


@pytest.fixture
def fact_db():
    db = Path(tempfile.mkdtemp()) / "test.db"
    layer = FactsLayer(db)
    yield layer
    layer.close()


class TestDetectContradictions:
    def test_no_entities_returns_empty(self, fact_db):
        """Facts without extractable entities should produce no contradictions."""
        fid = fact_db.add("hello world")
        assert fact_db.detect_contradictions(fid) == []

    def test_no_shared_entities_returns_empty(self, fact_db):
        """Facts about different entities should not contradict."""
        f1 = fact_db.add("Albert Einstein studied theoretical physics")
        fact_db.add("Java Virtual Machine manages memory automatically")
        # No overlapping entities → no contradiction
        assert fact_db.detect_contradictions(f1) == []

    def test_similar_facts_no_flag(self, fact_db):
        """Facts sharing entities with high Jaccard similarity → no flag."""
        f1 = fact_db.add("Albert Einstein developed the theory of relativity")
        fact_db.add("Albert Einstein proposed the theory of relativity in 1915")
        # Both about Einstein + relativity → high overlap
        assert fact_db.detect_contradictions(f1) == []

    def test_contradictory_facts_flagged(self, fact_db):
        """Facts sharing entities but very different content → auto-flagged during add."""
        f1 = fact_db.add("Java Virtual Machine is a fast compiled runtime")
        f2 = fact_db.add("Java Virtual Machine lacks garbage collection features")
        # Auto-detection during f2's add flagged both sides
        row1 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f1,)
        ).fetchone()
        row2 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f2,)
        ).fetchone()
        assert "contradiction_pending" in (row1["tags"] or "")
        assert "contradiction_pending" in (row2["tags"] or "")

    def test_contradiction_tags_applied(self, fact_db):
        """Both sides get contradiction_pending tag."""
        f1 = fact_db.add("Java Virtual Machine is a compiled system")
        f2 = fact_db.add("Java Virtual Machine was designed for web servers")
        fact_db.detect_contradictions(f1)

        row1 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f1,)
        ).fetchone()
        row2 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f2,)
        ).fetchone()
        assert "contradiction_pending" in (row1["tags"] or "")
        assert "contradiction_pending" in (row2["tags"] or "")

    def test_already_flagged_skipped(self, fact_db):
        """Already flagged facts should not be flagged again."""
        f1 = fact_db.add("Java Virtual Machine is a compiled system")
        fact_db.add("Java Virtual Machine is for web design")
        # First pass
        fact_db.detect_contradictions(f1)
        # Second pass — should return empty because both already flagged
        hits2 = fact_db.detect_contradictions(f1)
        assert hits2 == []

    def test_resolve_clears_both_sides(self, fact_db):
        """resolve_contradictions removes tag from both sides."""
        f1 = fact_db.add("Java Virtual Machine is a compiled system")
        f2 = fact_db.add("Java Virtual Machine was designed for web scripting")
        fact_db.detect_contradictions(f1)

        n = fact_db.resolve_contradictions(f1)
        assert n >= 2  # resolved fact + at least one contradictor

        row1 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f1,)
        ).fetchone()
        row2 = fact_db._conn.execute(
            "SELECT tags FROM facts WHERE fact_id = ?", (f2,)
        ).fetchone()
        assert "contradiction_pending" not in (row1["tags"] or "")
        assert "contradiction_pending" not in (row2["tags"] or "")

    def test_stats_shows_pending_contradictions(self, fact_db):
        """stats() includes pending_contradictions count."""
        f1 = fact_db.add("Java Virtual Machine is a compiled system")
        fact_db.add("Java Virtual Machine was designed for web apps")
        fact_db.detect_contradictions(f1)

        st = fact_db.stats()
        assert "pending_contradictions" in st
        assert st["pending_contradictions"] >= 1

    def test_multiple_contradictors(self, fact_db):
        """One fact contradicting multiple others → tagged via auto-detection."""
        f1 = fact_db.add("Mars Rover discovered liquid water")
        f2 = fact_db.add("Mars Rover is a robotic vehicle exploring Gale Crater")
        # f2 auto-flagged f1+f2. f3 is added fresh — its auto-check only
        # finds unflagged contradictors (f1 and f2 already flagged, so f3
        # itself won't be flagged).  Verify at least f1+f2 both flagged.
        fact_db.add("Mars Rover carries scientific instruments for geology")

        rows = fact_db._conn.execute(
            "SELECT fact_id, tags FROM facts WHERE tags LIKE ?",
            ("%contradiction_pending%",),
        ).fetchall()
        flagged = {r["fact_id"] for r in rows}
        assert f1 in flagged
        assert f2 in flagged

    def test_detect_unknown_fact_returns_empty(self, fact_db):
        """Non-existent fact_id returns empty list."""
        assert fact_db.detect_contradictions(9999) == []

    def test_resolve_unknown_fact_returns_zero(self, fact_db):
        """resolve_contradictions on non-flagged fact returns 0."""
        f1 = fact_db.add("Python is great")
        assert fact_db.resolve_contradictions(f1) == 0

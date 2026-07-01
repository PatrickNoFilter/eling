"""Tests for eling_think — 8th tool, synthesis + gap-analysis (Task 12.5)."""

from __future__ import annotations

import pytest

from eling.brain import Brain


@pytest.fixture
def brain():
    """A fresh brain with a known facts.db."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    b = Brain(home=tmp)
    # Seed with controlled facts
    b.remember("Python is a high-level programming language", layer="facts", category="code")
    b.remember("Python was created by Guido van Rossum", layer="facts", category="code")
    b.remember("JavaScript runs in the browser", layer="facts", category="code")
    yield b
    b.close()


class TestThinkBasic:
    def test_think_returns_synthesis_and_results(self, brain):
        result = brain.think("Python")
        assert "synthesis" in result
        assert "results" in result
        assert "gap_analysis" in result
        assert result["gap_analysis"]["unknown_count"] == 0
        # At least one Python result across layers
        assert len(result["results"]) >= 1

    def test_think_unknown_topic(self, brain):
        result = brain.think("Quantum teleportation protocol")
        assert result["gap_analysis"]["unknown_count"] == 1
        assert "no relevant facts" in result["synthesis"].lower()

    def test_think_with_reason_entities(self, brain):
        """Pass entities to also run reason()."""
        result = brain.think("programming", entities=["Python", "JavaScript"])
        assert "reason_results" in result
        # reason might be empty but key must exist
        assert isinstance(result["reason_results"], list)

    def test_think_empty_query(self, brain):
        """Empty query returns no results."""
        result = brain.think("")
        assert len(result["results"]) == 0
        assert result["gap_analysis"]["unknown_count"] == 1


class TestThinkGapAnalysis:
    def test_stale_fact_detected(self, brain):
        """A fact with artificially lowered strength shows as stale."""
        import sqlite3
        db = str(brain.facts.db_path)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE facts SET strength = 0.3 WHERE content = 'Python is a high-level programming language'")
        conn.commit()
        conn.close()

        result = brain.think("Python")
        ga = result["gap_analysis"]
        # Low-strength fact should be flagged as stale
        stale_contents = [f["content"] for f in ga["stale_facts"]]
        assert any("high-level" in c for c in stale_contents)
        assert ga["stale_count"] >= 1

    def test_contradicted_fact_detected(self, brain):
        """A fact tagged with contradiction_pending shows as contradicted."""
        import sqlite3
        db = str(brain.facts.db_path)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE facts SET tags = 'contradiction_pending' WHERE content = 'Python is a high-level programming language'")
        conn.commit()
        conn.close()

        result = brain.think("Python")
        ga = result["gap_analysis"]
        contradicted_contents = [f["content"] for f in ga["contradicted_facts"]]
        assert any("high-level" in c for c in contradicted_contents)
        assert ga["contradicted_count"] >= 1

    def test_gap_analysis_empty_no_stale_no_contradicted(self, brain):
        """Fresh facts — no stale, no contradicted."""
        result = brain.think("JavaScript")
        ga = result["gap_analysis"]
        assert ga["stale_count"] == 0
        assert ga["contradicted_count"] == 0


class TestThinkMCPTools:
    """Verify the MCP tool definition is registered."""

    def test_think_tool_in_list(self):
        from eling.mcp_server import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "eling_think" in names
        tool = next(t for t in TOOLS if t["name"] == "eling_think")
        assert "query" in tool["inputSchema"]["required"]
        props = tool["inputSchema"]["properties"]
        assert "entities" in props
        assert "limit" in props

    def test_eight_tools_total(self):
        from eling.mcp_server import TOOLS
        assert len(TOOLS) == 8  # was 7, now 8 with eling_think

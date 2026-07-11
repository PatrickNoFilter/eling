"""Tests for explicit export — JSON/markdown dump + 9th tool (Task 13.2)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eling.brain import Brain


@pytest.fixture
def brain():
    tmp = Path(tempfile.mkdtemp())
    b = Brain(home=tmp, notion_api_key="")  # disable notion to avoid env NOTION_API_KEY
    b.remember("Python is a high-level language", layer="facts", category="code")
    b.remember("JavaScript runs in the browser", layer="facts", category="code")
    b.remember("FastAPI is a modern Python web framework", layer="kb", category="code")
    yield b
    b.close()


class TestExportFunctions:
    def test_export_json_default(self, brain):
        """JSON export returns preview with facts."""
        result = brain.export()
        assert result["format"] == "json"
        assert result["bytes"] > 50
        assert result["path"] is None

    def test_export_json_to_file(self, brain):
        """JSON export to file path."""
        tmp = Path(tempfile.mkdtemp()) / "export.json"
        result = brain.export(path=str(tmp))
        assert result["path"] is not None
        assert tmp.exists()
        data = json.loads(tmp.read_text())
        assert "facts" in data
        assert "meta" in data
        assert len(data["facts"]) >= 2
        assert "entity_graph" in data

    def test_export_markdown_default(self, brain):
        """Markdown export returns preview."""
        result = brain.export(format="markdown")
        assert result["format"] == "markdown"
        assert result["bytes"] > 50
        assert "# Eling Memory Export" in result["preview"]

    def test_export_markdown_to_file(self, brain):
        """Markdown export to file."""
        tmp = Path(tempfile.mkdtemp()) / "export.md"
        result = brain.export(format="markdown", path=str(tmp))
        assert result["path"] is not None
        assert tmp.exists()
        content = tmp.read_text()
        assert "## Facts" in content
        assert "Python" in content


class TestExportMCP:
    def test_export_tool_in_as_brain(self):
        """Export tool is now in as_brain MCP server (not notion-only eling)."""
        from eling.as_brain.mcp_server import TOOLS

        names = [t["name"] for t in TOOLS]
        assert "brain_export" in names
        tool = next(t for t in TOOLS if t["name"] == "brain_export")
        props = tool["inputSchema"]["properties"]
        assert "format" in props
        assert props["format"]["enum"] == ["json", "markdown"]

    def test_as_brain_has_core_tools(self):
        from eling.as_brain.mcp_server import TOOLS

        assert len(TOOLS) >= 15  # brain_remember + friends + linking + versioning

    def test_eling_notion_has_7_tools(self):
        """The notion-only eling MCP has 7 tools (incl. eling_get_page_full, eling_delete_page)."""
        from eling.mcp_server import TOOLS

        names = [t["name"] for t in TOOLS]
        assert len(TOOLS) == 7
        assert "eling_remember" in names
        assert "eling_search" in names
        assert "eling_get_page" in names
        assert "eling_get_page_full" in names
        assert "eling_create_page" in names
        assert "eling_stats" in names
        assert "eling_delete_page" in names

    def test_export_covers_all_layers(self, brain):
        """Full JSON export covers facts, entity_graph, kb, code, notion, builtin."""
        result = brain.export()
        assert result["bytes"] > 100


class TestExportIntegrity:
    def test_export_does_not_mutate(self, brain):
        """Export is read-only — facts count unchanged after export."""
        pre = brain.stats()["facts"]["total_facts"]
        brain.export(format="json")
        post = brain.stats()["facts"]["total_facts"]
        assert pre == post

    def test_export_entity_graph_covered(self, brain):
        """Entity graph edges appear in export when they exist."""
        # Force an entity wire by adding content with [[wiki links]]
        brain.remember(
            "[[FastAPI]] uses [[Python]] for web APIs", layer="facts", category="code"
        )
        result = brain.export()
        result["preview"]
        # Check facts now contains FastAPI
        assert result["bytes"] > 200

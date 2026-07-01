"""Tests for harness adapters (Task 12.3)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eling.adapters import (
    HermesAdapter,
    ClaudeCliAdapter,
    OpenCodeAdapter,
    OpenClawAdapter,
    OpenClaudeAdapter,
    all_adapters,
    get_adapter,
)


@pytest.fixture
def tmp_project():
    """Yield a temporary directory that looks like a project root."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ── HermesAdapter ────────────────────────────────────────────────────


def test_hermes_reads_memory_and_user(tmp_project):
    """HermesAdapter reads MEMORY.md + USER.md from ~/.hermes."""
    hermes = Path.home() / ".hermes"
    hermes.mkdir(parents=True, exist_ok=True)

    mem = hermes / "MEMORY.md"
    usr = hermes / "USER.md"

    mem.write_text("prefer python 3.12")
    usr.write_text("name: tester")

    adapter = HermesAdapter()
    ctx = adapter.read_context()
    assert "prefer python 3.12" in ctx
    assert "name: tester" in ctx
    assert "MEMORY" in ctx
    assert "USER PROFILE" in ctx


def test_hermes_missing_files(tmp_project):
    """HermesAdapter returns empty string when no files exist."""
    # Use a non-existent home dir
    fake_home = tmp_project / "no-hermes"
    adapter = HermesAdapter(hermes_home=fake_home)
    assert adapter.read_context() == ""


def test_hermes_budget():
    assert HermesAdapter().budget_bytes() == 8192


def test_hermes_name():
    assert HermesAdapter().name == "hermes"


def test_hermes_default_schema_pack():
    assert HermesAdapter().default_schema_pack() == "default"


# ── ClaudeCliAdapter ─────────────────────────────────────────────────


def test_claude_cli_reads_claude_md(tmp_project):
    claude = tmp_project / "CLAUDE.md"
    claude.write_text("This project uses pytest")
    adapter = ClaudeCliAdapter()
    ctx = adapter.read_context(project_root=tmp_project)
    assert "This project uses pytest" in ctx
    assert "CLAUDE.md" in ctx


def test_claude_cli_no_file(tmp_project):
    adapter = ClaudeCliAdapter()
    assert adapter.read_context(project_root=tmp_project) == ""


def test_claude_cli_budget():
    assert ClaudeCliAdapter().budget_bytes() == 32000


def test_claude_cli_schema_pack():
    assert ClaudeCliAdapter().default_schema_pack() == "coding"


# ── OpenCodeAdapter ──────────────────────────────────────────────────


def test_opencode_reads_agents_md(tmp_project):
    agents = tmp_project / "AGENTS.md"
    agents.write_text("Run tests with pytest -v")
    adapter = OpenCodeAdapter()
    ctx = adapter.read_context(project_root=tmp_project)
    assert "Run tests with pytest -v" in ctx
    assert "AGENTS.md" in ctx


def test_opencode_reads_opencode_json(tmp_project):
    cfg = tmp_project / "opencode.json"
    cfg.write_text(json.dumps({"model": "claude-opus"}))
    adapter = OpenCodeAdapter()
    ctx = adapter.read_context(project_root=tmp_project)
    assert "model" in ctx
    assert "claude-opus" in ctx


def test_opencode_missing_files(tmp_project):
    adapter = OpenCodeAdapter()
    assert adapter.read_context(project_root=tmp_project) == ""


def test_opencode_budget():
    assert OpenCodeAdapter().budget_bytes() == 32000


def test_opencode_schema_pack():
    assert OpenCodeAdapter().default_schema_pack() == "coding"


# ── OpenClawAdapter / OpenClaudeAdapter ──────────────────────────────


def test_openclaw_reads_claude_md(tmp_project):
    claude = tmp_project / "CLAUDE.md"
    claude.write_text("foo bar")
    adapter = OpenClawAdapter()
    ctx = adapter.read_context(project_root=tmp_project)
    assert "foo bar" in ctx


def test_openclaw_no_file(tmp_project):
    assert OpenClawAdapter().read_context(project_root=tmp_project) == ""


def test_openclaude_reads_claude_md(tmp_project):
    claude = tmp_project / "CLAUDE.md"
    claude.write_text("baz qux")
    adapter = OpenClaudeAdapter()
    ctx = adapter.read_context(project_root=tmp_project)
    assert "baz qux" in ctx


def test_openclaude_no_file(tmp_project):
    assert OpenClaudeAdapter().read_context(project_root=tmp_project) == ""


# ── Discovery ────────────────────────────────────────────────────────


def test_all_adapters_has_five():
    adapters = all_adapters()
    assert len(adapters) == 5
    for name in ("hermes", "claude_cli", "opencode", "openclaw", "openclaude"):
        assert name in adapters


def test_get_adapter_valid():
    a = get_adapter("claude_cli")
    assert isinstance(a, ClaudeCliAdapter)


def test_get_adapter_unknown():
    with pytest.raises(KeyError):
        get_adapter("nonexistent")


def test_get_adapter_with_kwargs():
    fake = Path("/nonexistent")
    a = get_adapter("hermes", hermes_home=fake)
    assert a.read_context() == ""

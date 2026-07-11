"""Tests for permissions enforcement (Task 12.4)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eling.permissions import (
    check_access,
    load_permissions,
    describe_permissions,
)


@pytest.fixture
def perms_file():
    """Create a temporary permissions JSON file."""
    data = {
        "sources": {
            "hermes": {"facts": "write", "kb": "write", "code": "write"},
            "claude": {"facts": "write", "kb": "none", "code": "read"},
            "opencode": {"facts": "read", "kb": "none", "code": "write"},
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def loaded(perms_file):
    """Pre-loaded permissions dict from the fixture file."""
    return load_permissions(perms_file)


# ── check_access ─────────────────────────────────────────────────────


class TestCheckAccess:
    def test_write_allowed(self, loaded):
        assert check_access("hermes", "facts", "write", loaded) is True

    def test_write_denied_none(self, loaded):
        assert check_access("claude", "kb", "write", loaded) is False

    def test_read_allowed_when_read_level(self, loaded):
        assert check_access("opencode", "facts", "read", loaded) is True

    def test_write_denied_when_read_level(self, loaded):
        assert check_access("opencode", "facts", "write", loaded) is False

    def test_unlisted_source_full_access(self, loaded):
        """Sources not in the file get full access."""
        assert check_access("unknown_agent", "facts", "write", loaded) is True

    def test_unlisted_layer_full_access(self, loaded):
        """Layers not defined for a source get 'write' default."""
        assert check_access("claude", "notion", "write", loaded) is True

    def test_no_perms_file(self):
        """No file → full access."""
        assert check_access("any", "facts", "write") is True

    def test_read_on_none_is_denied(self, loaded):
        assert check_access("claude", "kb", "read", loaded) is False

    def test_invalid_source_cfg_falls_open(self):
        assert check_access("bad", "facts", "write", {"sources": "scalar"}) is True

    def test_empty_perms_is_full_access(self):
        assert check_access("x", "y", "write", {}) is True


# ── load_permissions ─────────────────────────────────────────────────


class TestLoadPermissions:
    def test_loads_valid_file(self, perms_file):
        data = load_permissions(perms_file)
        assert "sources" in data
        assert "hermes" in data["sources"]

    def test_missing_file_returns_empty(self):
        assert load_permissions("/nonexistent") == {}

    def test_invalid_json_returns_empty(self):
        p = Path("/tmp/_bad_perms.json")
        p.write_text("not json")
        assert load_permissions(p) == {}
        p.unlink(missing_ok=True)

    def test_empty_file_returns_empty(self):
        p = Path("/tmp/_empty_perms.json")
        p.write_text("")
        assert load_permissions(p) == {}
        p.unlink(missing_ok=True)


# ── describe_permissions ─────────────────────────────────────────────


class TestDescribePermissions:
    def test_describe_length(self, loaded):
        rows = describe_permissions(loaded)
        # 3 sources × 5 layers = 15 rows
        assert len(rows) == 15

    def test_describe_contains_expected_source(self, loaded):
        rows = describe_permissions(loaded)
        sources = {r["source"] for r in rows}
        assert "hermes" in sources

    def test_describe_access_levels(self, loaded):
        rows = describe_permissions(loaded)
        claude_kb = [r for r in rows if r["source"] == "claude" and r["layer"] == "kb"]
        assert claude_kb
        assert claude_kb[0]["access"] == "none"

    def test_empty_perms_describe(self):
        assert describe_permissions({}) == []

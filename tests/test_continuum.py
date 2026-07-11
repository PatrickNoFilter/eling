"""Tests for the Continuum Layer 6 orchestration tier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eling.continuum import ContinuumStore
from eling.continuum.mcp_server import _handle
from eling.continuum.plot import apply_unified_diff, seed_plot


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "continuum.db"
    s = ContinuumStore(db_path=db)
    yield s
    s.close()


# ── store: projects ──


def test_project_create_and_get(store):
    r = store.project_create("/tmp/demo", name="demo")
    assert r["path"] == str(Path("/tmp/demo").resolve())
    assert r["name"] == "demo"
    got = store.project_get("/tmp/demo")
    assert got is not None
    assert got["name"] == "demo"


def test_project_list_empty_then_one(store):
    assert store.project_list() == []
    store.project_create("/tmp/a")
    assert len(store.project_list()) == 1


# ── store: two-tier knowledge ──


def test_knowledge_two_tier(store):
    store.project_create("/tmp/demo")
    store.knowledge_create(
        "/tmp/demo", "rule1", "base branch is main", kind="fundamental"
    )
    store.knowledge_create(
        "/tmp/demo", "gotcha1", "auth guard order matters", kind="situational"
    )
    # fundamental only
    fund = store.knowledge_list("/tmp/demo", kind="fundamental")
    assert len(fund) == 1 and fund[0]["kind"] == "fundamental"
    # get body
    body = store.knowledge_get("/tmp/demo", "gotcha1")
    assert body["content"] == "auth guard order matters"
    # search returns metadata
    hits = store.knowledge_search("/tmp/demo", "auth guard")
    assert any(h["slug"] == "gotcha1" for h in hits)


def test_knowledge_invalid_kind(store):
    store.project_create("/tmp/demo")
    with pytest.raises(ValueError):
        store.knowledge_create("/tmp/demo", "x", "y", kind="bogus")


def test_knowledge_whole_content_replace(store):
    store.project_create("/tmp/demo")
    store.knowledge_create("/tmp/demo", "k", "original")
    store.knowledge_create("/tmp/demo", "k", "updated body")
    assert store.knowledge_get("/tmp/demo", "k")["content"] == "updated body"


# ── store: agent state machine + collisions ──


def test_agent_state_machine_valid(store):
    store.project_create("/tmp/demo")
    store.agent_register("/tmp/demo", "a1", reserved_paths=["src/x.py"])
    store.agent_update("/tmp/demo", "a1", status="active")
    store.agent_update("/tmp/demo", "a1", status="merged", merged_commit="abc1234")
    rec = store.agent_get("/tmp/demo", "a1")
    assert rec["status"] == "merged"


def test_agent_invalid_transition(store):
    store.project_create("/tmp/demo")
    store.agent_register("/tmp/demo", "a1")
    with pytest.raises(ValueError):
        store.agent_update("/tmp/demo", "a1", status="merged")  # not active first


def test_agent_merged_requires_sha(store):
    store.project_create("/tmp/demo")
    store.agent_register("/tmp/demo", "a1")
    store.agent_update("/tmp/demo", "a1", status="active")
    with pytest.raises(ValueError):
        store.agent_update(
            "/tmp/demo", "a1", status="merged", merged_commit="abc"
        )  # too short


def test_reservation_collision_detected(store):
    store.project_create("/tmp/demo")
    store.agent_register("/tmp/demo", "a1", reserved_paths=["src/x.py"])
    store.agent_update("/tmp/demo", "a1", status="active")
    # a2 tries to reserve the same path -> warning
    res = store.agent_register("/tmp/demo", "a2", reserved_paths=["src/x.py"])
    assert "reservation_warning" in res
    assert res["reservation_warning"][0]["agent"] == "a1"


def test_registry_list_filter(store):
    store.project_create("/tmp/demo")
    store.agent_register("/tmp/demo", "a1")
    store.agent_register("/tmp/demo", "a2")
    store.agent_update("/tmp/demo", "a1", status="active")
    active = store.registry_list("/tmp/demo", status="active")
    assert [a["slug"] for a in active] == ["a1"]


# ── plot ──


def test_plot_seed_and_get(store):
    store.project_create("/tmp/demo")
    c = store.plot_get("/tmp/demo")
    assert c is None
    store.plot_set("/tmp/demo", seed_plot("demo"))
    assert "Phase 1" in store.plot_get("/tmp/demo")


def test_plot_unified_diff_apply():
    orig = "line1\nline2\nline3\n"
    diff = "@@ -2,1 +2,1 @@\n-line2\n+line2-modified\n"
    new = apply_unified_diff(orig, diff)
    assert new == "line1\nline2-modified\nline3\n"


def test_plot_unified_diff_hunk_mismatch():
    orig = "line1\n"
    diff = "-does-not-exist\n"
    with pytest.raises(ValueError):
        apply_unified_diff(orig, diff)


# ── MCP protocol ──


def _call(name, args):
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    resp = _handle(req)
    assert resp["id"] == 1
    assert "error" not in resp, resp.get("error")
    return json.loads(resp["result"]["content"][0]["text"])


def test_mcp_tools_list():
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = _handle(req)
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "continuum_dispatch" in names
    assert "continuum_knowledge_search" in names


def test_mcp_dispatch_flow(monkeypatch, tmp_path):
    # Point continuum db at a temp file via env; stub git so worktree is skipped.
    monkeypatch.setenv("ELING_CONTINUUM_DB", str(tmp_path / "c.db"))
    monkeypatch.setattr("eling.continuum.mcp_server.wt.is_git_repo", lambda p: False)

    _call("continuum_project_create", {"path": str(tmp_path / "proj"), "name": "proj"})
    res = _call(
        "continuum_dispatch",
        {
            "project": str(tmp_path / "proj"),
            "slug": "feat-1",
            "goal": "add rate limit",
            "reserved_paths": ["src/api.py"],
        },
    )
    assert res["slug"] == "feat-1"
    assert "prompt" in res
    assert "not a git repo" in res.get("note", "")

    # registered as draft
    rec = _call(
        "continuum_agent_get", {"project": str(tmp_path / "proj"), "slug": "feat-1"}
    )
    assert rec["status"] == "draft"

    # knowledge round trip through MCP
    _call(
        "continuum_knowledge_create",
        {
            "project": str(tmp_path / "proj"),
            "slug": "k1",
            "content": "register rate limit after auth guard",
            "kind": "situational",
            "embed": False,
        },
    )
    hits = _call(
        "continuum_knowledge_search",
        {"project": str(tmp_path / "proj"), "q": "rate limit"},
    )
    assert any(h["slug"] == "k1" for h in hits["results"])


def test_mcp_initialize_handshake():
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "claude_code", "version": "1.0"}},
    }
    resp = _handle(req)
    assert resp["result"]["serverInfo"]["name"] == "eling-continuum"

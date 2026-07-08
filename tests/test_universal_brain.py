"""Tests for the universal-brain gaps:

Gap #1 — auto agent source from the MCP `initialize` handshake.
Gap #2 — ELING_HOME env override honoured by the as_brain server.
Gap #3 — open verify-on-stop to all agents via ELING_VERIFY_ALL_AGENTS.
"""

import importlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from eling import verify_on_stop as vos


# ── Gap #1: handshake source auto-attribution ────────────────────────────────


class TestGap1HandshakeSource:
    """The as_brain MCP server should capture the handshake client name and
    use it as the default `source` for brain_remember."""

    def test_handshake_source_defaults_to_mcp(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        assert srv._handshake_source == "mcp"

    def test_initialize_captures_client_name(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        resp = srv._handle_initialize(
            1, {"clientInfo": {"name": "opencode", "version": "1.2.3"}}
        )
        assert resp["result"]["serverInfo"]["name"] == "as-brain"
        assert srv._handshake_source == "opencode"

    def test_initialize_ignores_unknown_client(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        srv._handle_initialize(1, {"clientInfo": {"name": "unknown"}})
        assert srv._handshake_source == "mcp"

    def test_brain_remember_defaults_source_to_handshake(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        srv._handshake_source = "myagent"

        captured = {}

        def fake_brain_remember(**kwargs):
            captured.update(kwargs)
            return {"layer": "facts", "id": 1}

        with patch.object(srv, "_get_brain") as get_brain:
            brain = get_brain.return_value
            brain.remember.side_effect = fake_brain_remember
            srv._handle_tool_call(
                1, {"name": "brain_remember", "arguments": {"content": "remember this"}}
            )
        assert captured.get("source") == "myagent"

    def test_brain_remember_explicit_source_wins(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        srv._handshake_source = "myagent"

        captured = {}

        def fake_brain_remember(**kwargs):
            captured.update(kwargs)
            return {"layer": "facts", "id": 1}

        with patch.object(srv, "_get_brain") as get_brain:
            brain = get_brain.return_value
            brain.remember.side_effect = fake_brain_remember
            srv._handle_tool_call(
                1,
                {
                    "name": "brain_remember",
                    "arguments": {"content": "x", "source": "explicit"},
                },
            )
        assert captured.get("source") == "explicit"


# ── Gap #2: ELING_HOME override ───────────────────────────────────────────────


class TestGap2ElingHomeOverride:
    def test_resolve_home_returns_none_when_unset(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        with patch.dict(os.environ, {}, clear=True):
            assert srv._resolve_home() is None

    def test_resolve_home_returns_expanded_path(self):
        import eling.as_brain.mcp_server as srv

        importlib.reload(srv)
        with patch.dict(os.environ, {"ELING_HOME": "~/my-brain"}, clear=True):
            assert srv._resolve_home() == str(Path("~/my-brain").expanduser())

    def test_get_brain_passes_home_when_set(self):
        import eling.as_brain.mcp_server as srv
        from eling.brain import Brain

        importlib.reload(srv)
        with patch.dict(os.environ, {"ELING_HOME": "/tmp/eling-home-test"}, clear=True):
            with patch.object(srv, "_brain", None):
                with patch.object(Brain, "__init__", return_value=None) as ctor:
                    try:
                        srv._get_brain()
                    except Exception:
                        pass
                    ctor.assert_called_once()
                    _, kwargs = ctor.call_args
                    assert kwargs.get("home") == "/tmp/eling-home-test"


# ── Gap #3: open verify-on-stop to all agents ────────────────────────────────


class TestGap3AllAgentsVerify:
    def test_default_hermes_adapter_skips(self):
        vos.reset_ledger()
        with patch.dict(os.environ, {}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="hermes") is True

    def test_all_agents_flag_activates_hermes(self):
        vos.reset_ledger()
        with patch.dict(os.environ, {"ELING_VERIFY_ALL_AGENTS": "1"}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="hermes") is False

    def test_all_agents_flag_activates_any_adapter(self):
        vos.reset_ledger()
        with patch.dict(os.environ, {"ELING_VERIFY_ALL_AGENTS": "yes"}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="opencode") is False
            assert (
                vos.host_has_verify_on_stop(adapter="auto") is False
            )  # auto-detect → generic, normally active anyway

    def test_all_agents_flag_case_insensitive(self):
        vos.reset_ledger()
        with patch.dict(os.environ, {"ELING_VERIFY_ALL_AGENTS": "TRUE"}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="hermes") is False

    def test_brain_verify_active_under_all_agents(self):
        vos.reset_ledger()
        from eling.brain import Brain

        with patch.dict(os.environ, {"ELING_VERIFY_ALL_AGENTS": "1"}, clear=True):
            b = Brain(adapter="hermes")
            result = b.verify()
            assert result["host_has_verify"] is False
            assert result["active"] is True

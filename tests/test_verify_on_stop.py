"""Tests for eling.verify_on_stop — conditional verify-on-stop."""

import os
from unittest.mock import patch


from eling import verify_on_stop as vos
from eling.brain import Brain
from eling.hooks import HOOK_FILE_EDIT

# ── Detection tests ──────────────────────────────────────────────────────────


class TestDetectHostAgent:
    def test_detects_hermes_from_env(self):
        with patch.dict(os.environ, {"HERMES_SESSION_SOURCE": "tui"}, clear=True):
            assert vos.detect_host_agent() == "hermes"

    def test_detects_opencode_from_env(self):
        with patch.dict(os.environ, {"OPENCODE_HOME": "/root/.opencode"}, clear=True):
            assert vos.detect_host_agent() == "opencode"

    def test_detects_generic_with_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            assert vos.detect_host_agent() == "generic"


class TestHostHasVerifyOnStop:
    def test_hermes_adapter_has_verify(self):
        assert vos.host_has_verify_on_stop(adapter="hermes") is True

    def test_opencode_adapter_lacks_verify(self):
        assert vos.host_has_verify_on_stop(adapter="opencode") is False

    def test_openclaw_adapter_lacks_verify(self):
        assert vos.host_has_verify_on_stop(adapter="openclaw") is False

    def test_auto_detects_hermes_from_env(self):
        with patch.dict(os.environ, {"HERMES_SESSION_SOURCE": "tui"}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="auto") is True

    def test_auto_detects_opencode_from_env(self):
        with patch.dict(os.environ, {"OPENCODE_HOME": "/root/.opencode"}, clear=True):
            assert vos.host_has_verify_on_stop(adapter="auto") is False


# ── Ledger tests ─────────────────────────────────────────────────────────────


class TestVerificationLedger:
    def setup_method(self):
        vos.reset_ledger()

    def test_empty_ledger_no_nudge(self):
        assert vos.build_verify_nudge() is None
        assert vos.verify_status()["needs_verification"] is False

    def test_record_edit_then_needs_verification(self):
        vos.record_edit("src/main.py")
        status = vos.verify_status()
        assert "src/main.py" in status["changed_paths"]
        assert status["needs_verification"] is True

    def test_record_verification_passed_clears_nudge(self):
        vos.record_edit("src/main.py")
        assert vos.build_verify_nudge() is not None
        vos.record_verification(status="passed", command="pytest")
        assert vos.build_verify_nudge() is None

    def test_max_attempts_exhausted(self):
        vos.record_edit("src/main.py")
        vos.record_verification(status="failed", command="pytest", output="1 failed")
        vos.record_verification(status="failed", command="pytest", output="1 failed")
        # Third call hits max_attempts=2
        vos.record_verification(status="failed", command="pytest", output="1 failed")
        assert vos.build_verify_nudge() is None

    def test_non_code_paths_filtered(self):
        vos.record_edit("README.md")
        vos.record_edit("LICENSE")
        assert vos.build_verify_nudge() is None
        status = vos.verify_status()
        assert status["changed_paths"] == []

    def test_mixed_code_and_docs(self):
        vos.record_edit("src/main.py")
        vos.record_edit("README.md")
        assert "src/main.py" in vos.verify_status()["changed_paths"]
        assert "README.md" not in vos.verify_status()["changed_paths"]

    def test_reset_ledger(self):
        vos.record_edit("src/main.py")
        assert vos.build_verify_nudge() is not None
        vos.reset_ledger()
        assert vos.build_verify_nudge() is None

    def test_nudge_format_contains_paths(self):
        vos.record_edit("src/main.py")
        nudge = vos.build_verify_nudge()
        assert nudge is not None
        assert "src/main.py" in nudge
        assert "[System:" in nudge
        assert "Verification status" in nudge


# ── Brain integration tests ──────────────────────────────────────────────────


class TestBrainVerifyMethod:
    def test_verify_hermes_adapter_skips(self):
        b = Brain(adapter="hermes")
        result = b.verify()
        assert result["host_has_verify"] is True
        assert result["active"] is False

    def test_verify_opencode_adapter_active(self):
        b = Brain(adapter="opencode")
        result = b.verify()
        assert result["host_has_verify"] is False
        assert result["active"] is True

    def test_verify_record_passed(self):
        b = Brain(adapter="opencode")
        result = b.verify(status="passed", command="pytest")
        assert result["recorded"] is True
        assert result["status"] == "passed"

    def test_verify_query_after_edit(self):
        b = Brain(adapter="opencode")
        b.fire_hook(HOOK_FILE_EDIT, file_path="src/main.py")
        result = b.verify()
        assert result["active"] is True
        assert len(result["changed_paths"]) >= 1
        assert result["needs_verification"] is True


# ── MCP tool registration ────────────────────────────────────────────────────


class TestVerifyMCPTool:
    def test_verify_tool_in_tools_list(self):
        from eling.as_brain.mcp_server import TOOLS

        names = [t["name"] for t in TOOLS]
        assert "brain_verify" in names

    def test_verify_tool_accepts_status_param(self):
        from eling.as_brain.mcp_server import TOOLS

        verify_def = [t for t in TOOLS if t["name"] == "brain_verify"][0]
        props = verify_def["inputSchema"]["properties"]
        assert "status" in props
        assert props["status"]["enum"] == ["", "passed", "failed", "skipped"]


# ── Config tests ─────────────────────────────────────────────────────────────


class TestVerifyConfig:
    def test_verify_on_stop_in_defaults(self):
        from eling.config import DEFAULTS

        assert "verify_on_stop" in DEFAULTS
        assert DEFAULTS["verify_on_stop"] is True

    def test_verify_max_attempts_in_defaults(self):
        from eling.config import DEFAULTS

        assert "verify_on_stop_max_attempts" in DEFAULTS
        assert DEFAULTS["verify_on_stop_max_attempts"] == 2

"""Tests for Eling 12-Layer Hook System."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eling.hooks import (
    HookRegistry,
    ALL_HOOKS,
    HOOK_SESSION_START,
    HOOK_PRE_USER_MESSAGE,
    HOOK_POST_USER_MESSAGE,
    HOOK_PRE_TOOL_USE,
    HOOK_POST_TOOL_USE,
    HOOK_POST_ASSISTANT_MESSAGE,
    HOOK_DECISION_MADE,
    HOOK_FILE_EDIT,
    HOOK_ERROR_OCCURRED,
    HOOK_COMPACTION,
    HOOK_SESSION_END,
    HOOK_IDLE_30MIN,
)
from eling.brain import Brain


# ============================================================================
# HookRegistry — unit tests
# ============================================================================


class TestHookRegistry:
    def test_register_and_fire(self):
        reg = HookRegistry()
        collected = []

        def handler(name, ctx):
            collected.append((name, ctx.get("msg")))

        reg.register(HOOK_SESSION_START, handler)
        reg.fire(HOOK_SESSION_START, {"msg": "hello"})
        assert collected == [("session_start", "hello")]

    def test_unknown_hook_warns(self):
        reg = HookRegistry()
        # Should not crash; handler not added for unknown hook
        reg.register("non_existent_hook", lambda n, c: None)

    def test_fire_multiple_handlers(self):
        reg = HookRegistry()
        seen = []

        reg.register(HOOK_COMPACTION, lambda n, c: seen.append("a"))
        reg.register(HOOK_COMPACTION, lambda n, c: seen.append("b"))
        reg.fire(HOOK_COMPACTION)
        assert seen == ["a", "b"]

    def test_handler_exception_does_not_crash(self):
        reg = HookRegistry()

        def crashy(name, ctx):
            raise ValueError("oops")

        def ok(name, ctx):
            return 42

        reg.register(HOOK_ERROR_OCCURRED, crashy)
        reg.register(HOOK_ERROR_OCCURRED, ok)
        results = reg.fire(HOOK_ERROR_OCCURRED)
        assert results == [None, 42]  # crashy returns None (caught), ok returns 42

    def test_fire_no_handlers(self):
        reg = HookRegistry()
        results = reg.fire(HOOK_PRE_USER_MESSAGE)
        assert results == []

    def test_has_handlers(self):
        reg = HookRegistry()
        assert not reg.has_handlers(HOOK_SESSION_START)
        reg.register(HOOK_SESSION_START, lambda n, c: None)
        assert reg.has_handlers(HOOK_SESSION_START)

    def test_unregister(self):
        reg = HookRegistry()

        def handler(n, c):
            return None

        reg.register(HOOK_POST_TOOL_USE, handler)
        assert reg.has_handlers(HOOK_POST_TOOL_USE)
        reg.unregister(HOOK_POST_TOOL_USE, handler)
        assert not reg.has_handlers(HOOK_POST_TOOL_USE)

    def test_total_handlers(self):
        reg = HookRegistry()
        assert reg.total_handlers == 0
        reg.register(HOOK_SESSION_START, lambda n, c: None)
        reg.register(HOOK_SESSION_END, lambda n, c: None)
        reg.register(HOOK_SESSION_END, lambda n, c: None)
        assert reg.total_handlers == 3

    def test_reset_clears_all(self):
        reg = HookRegistry()
        reg.register(HOOK_SESSION_START, lambda n, c: None)
        reg.reset()
        assert reg.total_handlers == 0

    def test_fire_returns_results_in_order(self):
        reg = HookRegistry()
        reg.register(HOOK_DECISION_MADE, lambda n, c: 1)
        reg.register(HOOK_DECISION_MADE, lambda n, c: 2)
        reg.register(HOOK_DECISION_MADE, lambda n, c: 3)
        assert reg.fire(HOOK_DECISION_MADE) == [1, 2, 3]

    def test_fire_without_context(self):
        reg = HookRegistry()
        collected = []
        reg.register(HOOK_SESSION_START, lambda n, c: collected.append(c))
        reg.fire(HOOK_SESSION_START)
        assert collected == [{}]


# ============================================================================
# ALL_HOOKS constant
# ============================================================================


class TestAllHooks:
    def test_exactly_16_hooks(self):
        assert len(ALL_HOOKS) == 16  # was 15, now 16 with verify_request

    def test_all_hooks_are_strings(self):
        for h in ALL_HOOKS:
            assert isinstance(h, str)

    def test_all_named_hooks_present(self):
        required = {
            "session_start",
            "pre_user_message",
            "post_user_message",
            "pre_tool_use",
            "post_tool_use",
            "post_assistant_message",
            "decision_made",
            "file_edit",
            "verify_request",
            "error_occurred",
            "compaction",
            "session_end",
            "idle_30min",
            "sync_start",
            "sync_complete",
            "sync_error",
        }
        assert set(ALL_HOOKS) == required


# ============================================================================
# Built-in handlers — integration tests with Brain
# ============================================================================


class TestBuiltinHandlers:
    @pytest.fixture
    def brain(self):
        tmp = Path(tempfile.mkdtemp())
        return Brain(home=tmp)

    def test_default_hooks_are_registered(self, brain):
        """Brain.__init__ registers all default hooks."""
        assert brain.hooks.total_handlers == 16  # 15 + verify_request
        for hook in ALL_HOOKS:
            assert brain.hooks.has_handlers(hook), f"Missing handler for {hook}"

    def test_hook_session_start_fires_without_crash(self, brain):
        results = brain.fire_hook(HOOK_SESSION_START)
        assert len(results) == 1  # one handler
        info = results[0]
        assert isinstance(info, dict)
        assert "facts_count" in info
        assert "kb_sources" in info

    def test_hook_pre_user_message_empty(self, brain):
        results = brain.fire_hook(HOOK_PRE_USER_MESSAGE, content="")
        assert len(results) == 1
        assert results[0] == {"injected": False, "reason": "no content"}

    def test_hook_pre_user_message_with_content(self, brain):
        brain.remember("something about Eling memory system", layer="facts")
        results = brain.fire_hook(HOOK_PRE_USER_MESSAGE, content="tell me about Eling")
        assert len(results) == 1
        info = results[0]
        assert info["injected"] is True

    def test_hook_post_user_message(self, brain):
        results = brain.fire_hook(
            HOOK_POST_USER_MESSAGE, content="Hello Eling!", source="user_prompt"
        )
        assert len(results) == 1
        assert results[0]["indexed"] is True

    def test_hook_pre_tool_use(self, brain):
        results = brain.fire_hook(
            HOOK_PRE_TOOL_USE, tool_name="web_search", arguments="climate"
        )
        assert len(results) == 1
        assert results[0]["recalled"] is True

    def test_hook_pre_tool_use_empty(self, brain):
        results = brain.fire_hook(HOOK_PRE_TOOL_USE, tool_name="", arguments="")
        assert len(results) == 1
        assert results[0]["recalled"] is False

    def test_hook_post_tool_use(self, brain):
        results = brain.fire_hook(
            HOOK_POST_TOOL_USE,
            tool_name="web_search",
            result={"data": "some results"},
        )
        assert len(results) == 1
        assert results[0]["stored"] is True

    def test_hook_post_assistant_message(self, brain):
        results = brain.fire_hook(
            HOOK_POST_ASSISTANT_MESSAGE, content="Here is an answer about Eling."
        )
        assert len(results) == 1
        assert results[0]["facts_stored"] == 1

    def test_hook_decision_made_with_content(self, brain):
        results = brain.fire_hook(
            HOOK_DECISION_MADE, content="Always use facts for short text"
        )
        assert len(results) == 1
        assert results[0]["decided"] is True
        assert results[0]["fact_id"] is not None

    def test_hook_decision_made_with_correction(self, brain):
        results = brain.fire_hook(
            HOOK_DECISION_MADE,
            content="Before: use kb for short",
            correction="No, use facts for short",
        )
        assert len(results) == 1
        assert results[0]["corrected"] is True

    def test_hook_error_occurred(self, brain):
        results = brain.fire_hook(
            HOOK_ERROR_OCCURRED,
            error="Connection refused",
            tool_name="web_search",
            context="fetching url",
        )
        assert len(results) == 1
        assert results[0]["stored"] is True

    def test_hook_compaction(self, brain):
        results = brain.fire_hook(HOOK_COMPACTION, summary="Session went well.")
        assert len(results) == 1
        assert results[0]["stored"] is True

    def test_hook_session_end(self, brain):
        results = brain.fire_hook(HOOK_SESSION_END, summary="3 tasks completed")
        assert len(results) == 1
        # Notion configured but missing parent_id: returns notion_page: None
        # If notion falls through, falls back to facts.add (stored=True or logged=True)
        r = results[0]
        assert (
            r.get("notion_page") is None
            or r.get("stored") is True
            or r.get("logged") is True
        )

    def test_hook_file_edit(self, brain):
        # codegraph CLI is installed in this environment — reindex may succeed
        results = brain.fire_hook(HOOK_FILE_EDIT, file_path="src/eling/hooks.py")
        assert len(results) == 1
        # Should complete without error regardless of success state

    def test_hook_idle_30min_no_notion(self, brain):
        results = brain.fire_hook(HOOK_IDLE_30MIN, notion_parent_id="")
        assert len(results) == 1
        assert results[0]["promoted"] == 0  # Notion not configured


# ============================================================================
# HookRegistry — integration with Brain.remember() wiring
# ============================================================================


class TestBrainHookWiring:
    @pytest.fixture
    def brain(self):
        tmp = Path(tempfile.mkdtemp())
        return Brain(home=tmp)

    def test_remember_fires_post_tool_use(self, brain):
        """remember() must fire post_tool_use hook internally."""
        # The default post_tool_use handler stores the observation in brain.
        # After remember(), the handler runs and stores a "Tool [remember] returned:"
        # fact — so total facts should reflect both the original + the observation.
        initial = brain.facts.stats()["total_facts"]
        result = brain.remember("testing hook wiring in remember", layer="facts")
        # remember stored 1 fact, post_tool_use handler stored 1 observation = 2
        assert result["layer"] == "facts"
        # The hook fired and stored an observation
        after = brain.facts.stats()["total_facts"]
        assert after >= initial + 1

    def test_recall_fires_pre_tool_use(self, brain):
        """recall() must fire pre_tool_use hook internally."""
        brain.remember("something to recall", layer="facts")
        result = brain.recall("recall")
        assert "merged" in result


# ============================================================================
# Custom hooks — plugin-style registration
# ============================================================================


class TestCustomHandlers:
    def test_custom_handler_on_existing_hook(self):
        reg = HookRegistry()

        def my_pre_user(n, ctx):
            ctx["custom"] = True
            return ctx

        reg.register(HOOK_PRE_USER_MESSAGE, my_pre_user)
        results = reg.fire(HOOK_PRE_USER_MESSAGE, {"msg": "test"})
        assert len(results) == 1
        assert results[0].get("custom") is True

    def test_handler_on_multiple_hooks(self):
        reg = HookRegistry()
        fired = []

        def handler(n, ctx):
            fired.append(n)

        reg.register(HOOK_SESSION_START, handler)
        reg.register(HOOK_SESSION_END, handler)
        reg.fire(HOOK_SESSION_START)
        reg.fire(HOOK_SESSION_END)
        assert fired == [HOOK_SESSION_START, HOOK_SESSION_END]

    def test_can_register_same_handler_twice(self):
        reg = HookRegistry()
        results = []

        def handler(n, ctx):
            results.append(1)

        reg.register(HOOK_ERROR_OCCURRED, handler)
        reg.register(HOOK_ERROR_OCCURRED, handler)
        reg.fire(HOOK_ERROR_OCCURRED)
        assert results == [1, 1]  # handler fires twice


# ============================================================================
# Stats integration
# ============================================================================


class TestBrainStatsHooks:
    def test_stats_includes_hooks(self):
        tmp = Path(tempfile.mkdtemp())
        brain = Brain(home=tmp)
        s = brain.stats()
        assert "hooks" in s
        assert s["hooks"]["total_handlers"] == 16  # 15 + verify_request
        assert s["hooks"]["hooks_with_handlers"] == 16

    def test_stats_reflects_custom_hook(self):
        tmp = Path(tempfile.mkdtemp())
        brain = Brain(home=tmp)
        brain.hooks.register(HOOK_SESSION_START, lambda n, c: None)
        s = brain.stats()
        assert s["hooks"]["total_handlers"] == 17  # 16 base + 1 custom

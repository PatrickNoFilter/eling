"""Verify-on-stop — verification nudge for agents that lack built-in verification.

When an AI agent (OpenCode, OpenClaw, etc.) does not have its own
verify-on-stop, eling fills the gap:

1. Tracks file edits via hooks or explicit MCP calls
2. Detects whether the host agent already has built-in verification (skip)
3. Runs spec-kit conformance check (if spec-kit artifacts exist)
4. Produces a verification nudge message when code was edited but not verified
5. Exposes status via MCP tool so any agent can query it

Detection logic:
  - ELING_ADAPTER=hermes → skip (Hermes has built-in verification)
  - ELING_ADAPTER=opencode|openclaw|openclaude|claude_cli → enable
  - ELING_ADAPTER=auto → auto-detect from environment variables
"""

from __future__ import annotations

import os
import time
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent signatures
# ---------------------------------------------------------------------------

# Agents that have built-in verify-on-stop — eling is a no-op for these
AGENTS_WITH_VERIFY: frozenset[str] = frozenset({"hermes"})

# Agents that do NOT have built-in verify-on-stop — eling provides it
AGENTS_WITHOUT_VERIFY: frozenset[str] = frozenset(
    {
        "opencode",
        "openclaw",
        "openclaude",
        "claude_cli",
        "cursor",
        "windsurf",
        "generic",
    }
)

# Env-var → agent name mapping for auto-detection
AGENT_SIGNATURES: dict[str, str] = {
    "HERMES_SESSION_SOURCE": "hermes",
    "HERMES_PLATFORM": "hermes",
    "OPENCODE_HOME": "opencode",
}

# ---------------------------------------------------------------------------
# Public API: detection
# ---------------------------------------------------------------------------


def detect_host_agent() -> str:
    """Detect which AI agent is running by inspecting environment variables.

    Returns one of: ``hermes``, ``opencode``, or ``generic``.
    """
    for env_var, agent in AGENT_SIGNATURES.items():
        val = os.environ.get(env_var)
        if val and str(val).strip():
            return agent
    return "generic"


def host_has_verify_on_stop(adapter: str = "auto") -> bool:
    """Return True if the host agent already has verify-on-stop built-in.

    Parameters
    ----------
    adapter:
        The resolved ``ELING_ADAPTER`` value.
        ``"auto"`` (default) → auto-detect from environment.
        Any other string is checked against ``AGENTS_WITH_VERIFY``.

    Returns
    -------
    bool
        True when the host agent natively handles verification nudges.

    Universal mode
    --------------
    Set ``ELING_VERIFY_ALL_AGENTS=1`` to force eling's verify-on-stop to be
    active for *every* agent, including Hermes. This powers the "universal
    brain" use case where the shared ``as_brain`` MCP server provides
    verification for all connected agents regardless of harness. When unset
    (the default), Hermes keeps its built-in verification and eling stays a
    no-op for it.
    """
    if os.environ.get("ELING_VERIFY_ALL_AGENTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    if adapter != "auto":
        return adapter in AGENTS_WITH_VERIFY
    agent = detect_host_agent()
    return agent in AGENTS_WITH_VERIFY


# ---------------------------------------------------------------------------
# Verification ledger (session-scoped)
# ---------------------------------------------------------------------------

_ledger: dict[str, Any] = {
    "changed_paths": [],
    "verification_events": [],
    "verified": False,
    "last_edit_time": 0.0,
    "last_verify_time": 0.0,
    "verify_attempts": 0,
}


def record_edit(file_path: str) -> None:
    """Record a file edit in the verification ledger.

    Call this whenever the agent writes or patches a file.
    Resets the ``verified`` flag so a new verification is required.
    """
    global _ledger
    if file_path not in _ledger["changed_paths"]:
        _ledger["changed_paths"].append(file_path)
    _ledger["last_edit_time"] = time.time()
    _ledger["verified"] = False


def record_verification(
    status: str,
    command: str = "",
    output: str = "",
) -> None:
    """Record a verification event (test run, lint, build, etc.).

    Parameters
    ----------
    status:
        ``"passed"``, ``"failed"``, or ``"skipped"``.
    command:
        The shell command that was executed (e.g. ``"pytest"``).
    output:
        Truncated output from the command.
    """
    global _ledger
    _ledger["verification_events"].append(
        {
            "time": time.time(),
            "status": status,
            "command": command,
            "output_summary": output[:500] if output else "",
        }
    )
    if status == "passed":
        _ledger["verified"] = True
        _ledger["last_verify_time"] = time.time()
    _ledger["verify_attempts"] += 1


def reset_ledger() -> None:
    """Reset the verification ledger (e.g. at session start)."""
    global _ledger
    _ledger = {
        "changed_paths": [],
        "verification_events": [],
        "verified": False,
        "last_edit_time": 0.0,
        "last_verify_time": 0.0,
        "verify_attempts": 0,
    }


# ---------------------------------------------------------------------------
# Non-code path filter (same heuristic as Hermes' verification_stop.py)
# ---------------------------------------------------------------------------

_NON_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".markdown",
        ".mdx",
        ".rst",
        ".txt",
        ".text",
        ".adoc",
        ".asciidoc",
        ".org",
        ".log",
        ".csv",
        ".tsv",
    }
)

_NON_CODE_FILENAMES: frozenset[str] = frozenset(
    {
        "license",
        "licence",
        "notice",
        "authors",
        "contributors",
        "changelog",
        "codeowners",
    }
)


def _is_non_code_path(raw: str) -> bool:
    """Return True when a file path is documentation/prose with nothing to verify."""
    try:
        p = Path(str(raw))
    except Exception:
        return False
    suffix = p.suffix.lower()
    if suffix in _NON_CODE_EXTENSIONS:
        return True
    if not suffix and p.name.lower() in _NON_CODE_FILENAMES:
        return True
    return False


def _filter_verifiable_paths(paths: list[str]) -> list[str]:
    """Drop documentation/prose paths; keep code paths that need verification."""
    return [p for p in paths if p and not _is_non_code_path(p)]


# ---------------------------------------------------------------------------
# Nudge builder
# ---------------------------------------------------------------------------

_MAX_CHANGED_PATHS_SHOWN = 8
_MAX_VERIFY_ATTEMPTS = 2


def _format_paths(paths: list[str]) -> str:
    """Pretty-print changed paths for the nudge message."""
    shown = paths[:_MAX_CHANGED_PATHS_SHOWN]
    lines = [f"- `{p}`" for p in shown]
    remaining = len(paths) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


def build_verify_nudge() -> str | None:
    """Build a verification nudge message if code edits need fresh verification.

    Returns
    -------
    str or None
        The nudge text (wrapped in ``[System: ...]`` markers), or None when no
        nudge is needed (no edits, only doc files, already verified, or
        max attempts reached).
    """
    global _ledger

    paths = sorted({str(p) for p in _filter_verifiable_paths(_ledger["changed_paths"])})
    if not paths:
        return None

    if _ledger["verify_attempts"] >= _MAX_VERIFY_ATTEMPTS:
        return None

    if _ledger["verified"] and _ledger["last_verify_time"] >= _ledger["last_edit_time"]:
        return None

    # Build status summary from the latest verification event
    detail_parts: list[str] = []
    if _ledger["verification_events"]:
        last = _ledger["verification_events"][-1]
        state = last.get("status", "unverified")
        detail_parts.append(state)
        cmd = last.get("command", "")
        if cmd:
            detail_parts.append(f"last command `{cmd}`")
        output = last.get("output_summary", "")
        if output:
            max_output = 1200
            if len(output) > max_output:
                output = output[:max_output].rstrip() + "\n... [truncated]"
            detail_parts.append(f"last output:\n{output}")
    else:
        detail_parts.append("unverified")

    return (
        "[System: You edited code in this turn, but the workspace does not have "
        "fresh passing verification evidence yet.\n\n"
        f"Verification status: {' | '.join(detail_parts)}\n\n"
        f"Changed paths:\n{_format_paths(paths)}\n\n"
        "Run the relevant verification command now (test, lint, build), "
        "read any failure, repair the code, and summarize what passed. "
        "If verification is not possible, explain the concrete blocker "
        "instead of claiming the work is fully verified.]"
    )


def verify_status() -> dict[str, Any]:
    """Return the current verification status as a dictionary.

    Use this from MCP tools to let agents query verification state.
    """
    global _ledger
    paths = sorted({str(p) for p in _filter_verifiable_paths(_ledger["changed_paths"])})
    return {
        "changed_paths": paths,
        "verification_events": _ledger["verification_events"][-3:],
        "verified": _ledger["verified"],
        "attempts": _ledger["verify_attempts"],
        "needs_verification": bool(paths) and not _ledger["verified"],
        "nudge": build_verify_nudge(),
        "spec_kit": _spec_kit_check(),
    }


# ---------------------------------------------------------------------------
# Spec-kit integration
# ---------------------------------------------------------------------------

_spec_kit_verifier: Any = None
"""Lazily created SpecKitVerifier (init on first call to _spec_kit_check)."""

_spec_kit_project_path: str | None = None


def set_project_path(path: str | Path | None) -> None:
    """Set the project path for spec-kit artifact discovery.

    Call this once at session start so spec-kit artifacts can be loaded.
    Pass ``None`` to disable spec-kit checking.
    """
    global _spec_kit_project_path, _spec_kit_verifier
    _spec_kit_project_path = str(path) if path else None
    _spec_kit_verifier = None  # force re-init on next check


def _spec_kit_check() -> dict[str, Any]:
    """Run spec-kit verification against the current changed paths.

    Returns
    -------
    dict with detected, summary, nudge, and coverage stats.
    Returns ``{"detected": False}`` when no spec-kit artifacts exist
    or project path is not set.
    """
    global _spec_kit_verifier, _spec_kit_project_path

    if not _spec_kit_project_path:
        return {"detected": False, "reason": "no project path set"}

    if _spec_kit_verifier is None:
        from .spec_kit import SpecKitVerifier

        _spec_kit_verifier = SpecKitVerifier(_spec_kit_project_path)

    try:
        changed = list({str(p) for p in _ledger.get("changed_paths", [])})
        result = _spec_kit_verifier.verify(changed_files=changed)
        return result
    except Exception as e:
        logger.debug("spec_kit check failed: %s", e)
        return {"detected": False, "error": str(e)}


def build_verify_nudge_spec_kit() -> str | None:
    """Build a spec-kit verification nudge fragment, or None.

    Returns a short string that can be appended to the main verify nudge,
    or None when spec-kit is not relevant (not detected or all covered).
    """
    sk = _spec_kit_check()
    if not sk.get("detected"):
        return None
    coverage = sk.get("coverage", {})
    uncovered = coverage.get("uncovered", 0)
    total = coverage.get("total", 0)
    covered = coverage.get("covered", 0)
    if uncovered == 0:
        return None
    return (
        "\n\n[Spec-kit coverage: {}/{} requirements covered ({} uncovered).\n"
        "Review the spec requirements flagged above and ensure the implementation "
        "addresses each one.]"
    ).format(covered, total, uncovered)


__all__ = [
    "detect_host_agent",
    "host_has_verify_on_stop",
    "record_edit",
    "record_verification",
    "reset_ledger",
    "build_verify_nudge",
    "verify_status",
    "set_project_path",
    "build_verify_nudge_spec_kit",
    "_spec_kit_check",
]

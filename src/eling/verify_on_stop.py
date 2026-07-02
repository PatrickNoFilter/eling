"""Verify-on-stop — verification nudge for agents that lack built-in verification.

When an AI agent (OpenCode, OpenClaw, etc.) does not have its own
verify-on-stop, eling fills the gap:

1. Tracks file edits via hooks or explicit MCP calls
2. Detects whether the host agent already has built-in verification (skip)
3. Produces a verification nudge message when code was edited but not verified
4. Exposes status via MCP tool so any agent can query it

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
AGENTS_WITHOUT_VERIFY: frozenset[str] = frozenset({
    "opencode",
    "openclaw",
    "openclaude",
    "claude_cli",
    "cursor",
    "windsurf",
    "generic",
})

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
    """
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
    _ledger["verification_events"].append({
        "time": time.time(),
        "status": status,
        "command": command,
        "output_summary": output[:500] if output else "",
    })
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

_NON_CODE_EXTENSIONS: frozenset[str] = frozenset({
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
})

_NON_CODE_FILENAMES: frozenset[str] = frozenset({
    "license",
    "licence",
    "notice",
    "authors",
    "contributors",
    "changelog",
    "codeowners",
})


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

    paths = sorted(
        {str(p) for p in _filter_verifiable_paths(_ledger["changed_paths"])}
    )
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
    paths = sorted(
        {str(p) for p in _filter_verifiable_paths(_ledger["changed_paths"])}
    )
    return {
        "changed_paths": paths,
        "verification_events": _ledger["verification_events"][-3:],
        "verified": _ledger["verified"],
        "attempts": _ledger["verify_attempts"],
        "needs_verification": bool(paths) and not _ledger["verified"],
        "nudge": build_verify_nudge(),
    }


__all__ = [
    "detect_host_agent",
    "host_has_verify_on_stop",
    "record_edit",
    "record_verification",
    "reset_ledger",
    "build_verify_nudge",
    "verify_status",
]

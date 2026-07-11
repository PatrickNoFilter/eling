"""Steering rules generator — writes agent-specific rules files.

Detects which AI agent is in use (OpenCode, Cursor, Claude Code, Kiro, Gemini)
and writes steering rules that teach the agent when/how to use eling's MCP tools.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

RULES: dict[str, str] = {
    "memory": """# Eling Memory — Steering Rules
#
# Eling is your long-term memory layer (MCP server: eling).
# Use these tools to store and retrieve persistent information
# across conversations.

## When to STORE memories
- User states a preference or habit → `eling_remember`
- User corrects a previous answer → `eling_remember`
- You discover a project fact (language, framework, pattern) → `eling_remember`
- A decision is made about architecture or design → `eling_remember`
- Content < 500 chars → auto-routes to facts layer
- Content > 500 chars or has markdown headings → auto-routes to KB

## When to RETRIEVE memories
- At conversation start → `eling_recall` with the user's question
- When topic shifts → `eling_recall` with new topic keywords
- When asked "do you remember..." → `eling_recall`
- Before suggesting a solution → `eling_recall` for prior context

## When to PROBE
- User asks "what do you know about X" → `eling_probe` with entity name
- Before contradicting a statement → `eling_probe` to check existing facts

## Snapshot & Rollback
- Before destructive operations → `eling_snapshot` with reason
- After a mistake → `eling_list_snapshots` + `eling_rollback`
""",
    "session-lifecycle": """# Session Lifecycle — Eling Memory
#
# Bootstrap memory at conversation start, persist at end.

## Conversation Start
1. `eling_recall(query="<user's first message>")` — load relevant context
2. `eling_recall(query="session context")` — check for active session state

## Conversation End
1. `eling_remember(content="<session summary>", category="general")` — persist key info
2. If Notion is configured, `eling_sync(direction="push")` — push high-trust facts
""",
    "memory-hygiene": """# Memory Hygiene — Eling
#
# Proactive governance to keep memory healthy.

- Periodically call `eling_evolve` to merge near-duplicate facts
- Before running evolution, call `eling_snapshot(reason="pre_evolution")`
- Check `eling_stats` for pending contradictions
- Resolve contradictions with `eling_remember(correction)` or evolution
- If facts grow stale, run evolution with lower threshold
""",
}

CURSOR_RULES_TEMPLATE = """---
description: Eling Memory — {title}
globs: 
---
{content}
"""

CLAUDECODE_RULES_TEMPLATE = """# Eling Memory — {title}

{content}
"""

OPCODE_AGENTS_TEMPLATE = """## Eling Memory — {title}

{content}
"""


def detect_agent(project_path: Path) -> list[str]:
    """Detect which AI agents are configured in the project."""
    agents = []
    if (project_path / ".cursor" / "rules").is_dir():
        agents.append("cursor")
    if (project_path / ".claude" / "rules").is_dir():
        agents.append("claude_code")
    if (project_path / "AGENTS.md").is_file():
        agents.append("opencode")
    if (project_path / ".kiro").is_dir():
        agents.append("kiro")
    if (project_path / ".gemini").is_dir() or (project_path / "GEMINI.md").is_file():
        agents.append("gemini")
    # Also check env vars
    if os.environ.get("CURSOR_HOME") or os.environ.get("CURSOR_AGENT"):
        agents.append("cursor")
    if os.environ.get("OPENCODE_HOME"):
        agents.append("opencode")
    if not agents:
        agents.append("generic")
    return agents


def write_rules(
    project_root: str | Path, agents: list[str] | None = None, dry_run: bool = False
) -> list[dict[str, Any]]:
    """Write steering rules for detected agents.

    Parameters
    ----------
    project_root : str or Path
        Project root directory.
    agents : list of str, optional
        Agent types to write rules for. Auto-detected if None.
    dry_run : bool
        If True, only show what would be written.

    Returns
    -------
    list of dict with keys: agent, file, action.
    """
    root = Path(project_root).expanduser().resolve()
    if agents is None:
        agents = detect_agent(root)

    results: list[dict[str, Any]] = []

    for agent in agents:
        if agent == "cursor":
            results.extend(_write_cursor_rules(root, dry_run))
        elif agent == "claude_code":
            results.extend(_write_claude_rules(root, dry_run))
        elif agent == "opencode":
            results.extend(_write_opencode_rules(root, dry_run))
        elif agent in ("generic", "kiro", "gemini"):
            results.extend(_write_generic_rules(root, agent, dry_run))

    return results


def _write_cursor_rules(root: Path, dry_run: bool) -> list[dict[str, Any]]:
    rules_dir = root / ".cursor" / "rules"
    if not dry_run:
        rules_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key, content in RULES.items():
        filename = f"eling-memory-{key}.mdc"
        filepath = rules_dir / filename
        text = CURSOR_RULES_TEMPLATE.format(
            title=key.replace("-", " ").title(), content=content
        )
        action = "write"
        if filepath.exists():
            action = "update"
        if not dry_run:
            filepath.write_text(text.strip() + "\n")
        written.append({"agent": "cursor", "file": str(filepath), "action": action})
    return written


def _write_claude_rules(root: Path, dry_run: bool) -> list[dict[str, Any]]:
    rules_dir = root / ".claude" / "rules"
    if not dry_run:
        rules_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key, content in RULES.items():
        filename = f"eling-memory-{key}.md"
        filepath = rules_dir / filename
        text = CLAUDECODE_RULES_TEMPLATE.format(
            title=key.replace("-", " ").title(), content=content
        )
        action = "write"
        if filepath.exists():
            action = "update"
        if not dry_run:
            filepath.write_text(text.strip() + "\n")
        written.append(
            {"agent": "claude_code", "file": str(filepath), "action": action}
        )
    return written


def _write_opencode_rules(root: Path, dry_run: bool) -> list[dict[str, Any]]:
    agents_file = root / "AGENTS.md"
    action = "create"
    existing = ""
    if agents_file.exists():
        existing = agents_file.read_text(encoding="utf-8")
        action = "update"

    # Gather eling rules section
    sections = []
    for key, content in RULES.items():
        title = key.replace("-", " ").title()
        sections.append(
            OPCODE_AGENTS_TEMPLATE.format(title=title, content=content.strip())
        )
    new_section = "\n\n".join(sections)

    if dry_run:
        return [{"agent": "opencode", "file": str(agents_file), "action": action}]

    if "## Eling Memory" not in existing:
        # Append to existing AGENTS.md
        text = (
            existing.rstrip() + "\n\n" + new_section + "\n"
            if existing
            else new_section + "\n"
        )
        agents_file.write_text(text, encoding="utf-8")
    else:
        # Already has eling section — skip
        return [{"agent": "opencode", "file": str(agents_file), "action": "skipped"}]

    return [{"agent": "opencode", "file": str(agents_file), "action": action}]


def _write_generic_rules(root: Path, agent: str, dry_run: bool) -> list[dict[str, Any]]:
    filename = "ELING_MEMORY.md"
    filepath = root / filename
    action = "write"
    if filepath.exists():
        action = "update"
    if not dry_run:
        sections = [
            f"# Eling Memory — {key.replace('-', ' ').title()}\n\n{content.strip()}"
            for key, content in RULES.items()
        ]
        filepath.write_text("\n\n".join(sections) + "\n")
    return [{"agent": agent, "file": str(filepath), "action": action}]

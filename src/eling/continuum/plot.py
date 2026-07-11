"""Continuum Layer 6 — PLOT protocol.

The PLOT.md is the canonical orchestration protocol seeded into every project.
Continuum's convention: plot mutations flow through unified diffs (no full-text
writes) so a stale context is rejected. We implement a *minimal* unified-diff
applier good enough for the line-level edits the orchestrator generates, with a
clear error (``hunk_mismatch``) on failure — mirroring Continuum's behaviour.
"""

from __future__ import annotations

import re
from typing import List

# 4-phase dispatch protocol (Intake -> Research -> Verify -> Handoff)
PLOT_SEED = """# PLOT — {project_name}

> Canonical orchestration protocol for this project. Any MCP client that reads
> this becomes the orchestrator. Edit it through unified diffs, never full-text.

## Phase 1 — Intake
- Capture the goal, constraints, and acceptance criteria.
- Reserve paths the new agent will touch (collision-checked via `reserved_paths`).

## Phase 2 — Research
- `knowledge_list(kind='fundamental')` — load binding rules every dispatch.
- `knowledge_search(q=...)` — find situational lessons relevant to the task.
- Read the codebase; verify the plan against `fundamental` rules.

## Phase 3 — Verify
- Run the project's checks (tests, lint, build) before merge.
- Record the result with `eling_verify` / `brain_verify`.
- `agent_update(status='merged', merged_commit='<sha>')` only with a real SHA.

## Phase 4 — Handoff
- Persist what was learned: `knowledge_create(kind='situational', ...)`.
- `agent_update(status='merged')`; release reserved paths.
- The next agent on this project inherits everything above.
"""


def seed_plot(project_name: str) -> str:
    """Return the seeded PLOT.md content for a project."""
    return PLOT_SEED.format(project_name=project_name)


def apply_unified_diff(original: str, diff: str) -> str:
    """Apply a minimal unified diff to ``original``.

    Supports standard hunk headers ``@@ -old_start,old_count +new_start,new_count @@``
    to position edits, plus headerless sequential edits (each line consumed in order,
    matching Continuum's line-level plot edits). Raises ``ValueError('hunk_mismatch')``
    on a stale/!matching context so the caller can re-read and regenerate — the exact
    Continuum contract for plot mutations.
    """
    orig_lines = original.split("\n")
    out: List[str] = []
    orig_idx = 0
    diff_lines = diff.split("\n")
    i = 0
    pending_old_start = None  # set after an @@ header

    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                # 1-based old start -> 0-based index into orig_lines
                pending_old_start = int(m.group(1)) - 1
                # flush up to that position if we're behind it
                if pending_old_start > orig_idx:
                    out.extend(orig_lines[orig_idx:pending_old_start])
                    orig_idx = pending_old_start
            i += 1
            continue
        if line.startswith(" "):
            content = line[1:]
            if orig_idx < len(orig_lines) and orig_lines[orig_idx] == content:
                out.append(content)
                orig_idx += 1
            else:
                raise ValueError("hunk_mismatch: context line does not match original")
            i += 1
            continue
        if line.startswith("-"):
            content = line[1:]
            if orig_idx < len(orig_lines) and orig_lines[orig_idx] == content:
                orig_idx += 1  # drop it
            else:
                raise ValueError("hunk_mismatch: '-' line not found in original")
            i += 1
            continue
        if line.startswith("+"):
            out.append(line[1:])
            i += 1
            continue
        # blank / unknown line — skip
        i += 1

    if orig_idx < len(orig_lines):
        out.extend(orig_lines[orig_idx:])
    return "\n".join(out)

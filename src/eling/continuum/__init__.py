"""Continuum Layer 6 — orchestration tier for eling.

Adapts the ideas from github.com/pouyahasanamreji/continuum into a pure-Python
MCP server that sits on top of eling's 5-tier memory. One hub, every AI agent
connects: Claude Code, Codex, Cline, Hermes, Zero, OpenCode. Shared memory +
multi-agent orchestration (agent registry state machine, reserved-path
collision prevention, PLOT.md protocol, isolated git worktrees).

Runs natively on Termux / PRoot — no Node, no NestJS, no Astro, no numpy
required. Semantic search is optional (uses eling's embeddings layer when
available, else falls back to FTS5 BM25).
"""

from __future__ import annotations

from .mcp_server import run_stdio
from .store import ContinuumStore

__all__ = ["ContinuumStore", "run_stdio"]

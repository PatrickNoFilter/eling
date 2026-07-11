"""Harness adapters — multi-agent context injection (Task 12.3).

Each adapter knows how to read its platform's memory/project file(s)
and report its context budget so eling can adapt what it serves.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


__all__ = [
    "HarnessAdapter",
    "HermesAdapter",
    "ClaudeCliAdapter",
    "OpenCodeAdapter",
    "OpenClawAdapter",
    "OpenClaudeAdapter",
    "all_adapters",
]


# ── Context budgets (conservative estimates) ─────────────────────────

_HERMES_BUDGET = 8_192  # MEMORY.md + USER.md, typically 2-4 KB each
_CLAUDE_CLI_BUDGET = 32_000  # CLAUDE.md can be up to ~32 KB
_OPENCODE_BUDGET = 32_000  # AGENTS.md + opencode.json
_OPENCLAW_BUDGET = 24_000  # CLAUDE.md equivalent
_OPENCLAUDE_BUDGET = 24_000


# ── Base adapter ─────────────────────────────────────────────────────


class HarnessAdapter(ABC):
    """Base class for platform-specific harness adapters."""

    name: str = ""

    @abstractmethod
    def read_context(self, project_root: str | Path = ".") -> str:
        """Read the harness's memory/context file(s)."""
        ...

    @abstractmethod
    def budget_bytes(self) -> int:
        """Approximate context budget in bytes."""
        ...

    def default_schema_pack(self) -> str:
        """Recommended schema pack name for this harness."""
        return "default"

    def context_file_path(self, name: str, project_root: str | Path = ".") -> Path:
        """Resolve a context file name, searching cwd → parent dirs."""
        root = Path(project_root).resolve()
        for parent in [root] + list(root.parents):
            candidate = parent / name
            if candidate.is_file():
                return candidate
        return root / name


# ── Concrete adapters ────────────────────────────────────────────────


class HermesAdapter(HarnessAdapter):
    """Reads from the Hermes profile's MEMORY.md and USER.md."""

    name = "hermes"

    def __init__(self, hermes_home: str | Path | None = None):
        home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
        self._mem = home / "MEMORY.md"
        self._usr = home / "USER.md"

    def read_context(self, project_root: str | Path = ".") -> str:
        parts: list[str] = []
        for path, label in [(self._mem, "MEMORY"), (self._usr, "USER PROFILE")]:
            if path.is_file():
                parts.append(
                    f"--- {label} ---\n{path.read_text(encoding='utf-8', errors='replace')}"
                )
        return "\n\n".join(parts) if parts else ""

    def budget_bytes(self) -> int:
        return _HERMES_BUDGET


class ClaudeCliAdapter(HarnessAdapter):
    """Reads CLAUDE.md from the project root."""

    name = "claude_cli"

    def read_context(self, project_root: str | Path = ".") -> str:
        path = self.context_file_path("CLAUDE.md", project_root)
        if path.is_file():
            return f"--- CLAUDE.md ---\n{path.read_text(encoding='utf-8', errors='replace')}"
        return ""

    def budget_bytes(self) -> int:
        return _CLAUDE_CLI_BUDGET

    def default_schema_pack(self) -> str:
        return "coding"


class OpenCodeAdapter(HarnessAdapter):
    """Reads AGENTS.md + opencode.json from the project root."""

    name = "opencode"

    def read_context(self, project_root: str | Path = ".") -> str:
        root = Path(project_root).resolve()
        parts: list[str] = []

        agents_path = self.context_file_path("AGENTS.md", project_root)
        if agents_path.is_file():
            parts.append(
                f"--- AGENTS.md ---\n{agents_path.read_text(encoding='utf-8', errors='replace')}"
            )

        oc_path = root / "opencode.json"
        if oc_path.is_file():
            try:
                data = json.loads(oc_path.read_text(encoding="utf-8", errors="replace"))
                parts.append(f"--- opencode.json ---\n{json.dumps(data, indent=2)}")
            except (json.JSONDecodeError, OSError):
                parts.append("--- opencode.json ---\n(invalid)")

        return "\n\n".join(parts) if parts else ""

    def budget_bytes(self) -> int:
        return _OPENCODE_BUDGET

    def default_schema_pack(self) -> str:
        return "coding"


class OpenClawAdapter(HarnessAdapter):
    """Placeholder for OpenClaw (CLAUDE.md equivalent)."""

    name = "openclaw"

    def read_context(self, project_root: str | Path = ".") -> str:
        path = self.context_file_path("CLAUDE.md", project_root)
        if path.is_file():
            return f"--- CLAUDE.md ---\n{path.read_text(encoding='utf-8', errors='replace')}"
        return ""

    def budget_bytes(self) -> int:
        return _OPENCLAW_BUDGET

    def default_schema_pack(self) -> str:
        return "coding"


class OpenClaudeAdapter(HarnessAdapter):
    """Placeholder for OpenClaude (CLAUDE.md equivalent)."""

    name = "openclaude"

    def read_context(self, project_root: str | Path = ".") -> str:
        path = self.context_file_path("CLAUDE.md", project_root)
        if path.is_file():
            return f"--- CLAUDE.md ---\n{path.read_text(encoding='utf-8', errors='replace')}"
        return ""

    def budget_bytes(self) -> int:
        return _OPENCLAUDE_BUDGET

    def default_schema_pack(self) -> str:
        return "coding"


# ── Discovery ────────────────────────────────────────────────────────


def all_adapters() -> dict[str, HarnessAdapter]:
    """Return all adapter instances keyed by name."""
    return {
        "hermes": HermesAdapter(),
        "claude_cli": ClaudeCliAdapter(),
        "opencode": OpenCodeAdapter(),
        "openclaw": OpenClawAdapter(),
        "openclaude": OpenClaudeAdapter(),
    }


def get_adapter(name: str, **kwargs: Any) -> HarnessAdapter:
    """Get a single adapter by name, raising KeyError if unknown."""
    builders = {
        "hermes": HermesAdapter,
        "claude_cli": ClaudeCliAdapter,
        "opencode": OpenCodeAdapter,
        "openclaw": OpenClawAdapter,
        "openclaude": OpenClaudeAdapter,
    }
    cls = builders.get(name)
    if cls is None:
        raise KeyError(f"Unknown adapter: {name}. Valid: {', '.join(sorted(builders))}")
    return cls(**kwargs)

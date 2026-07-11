"""Builtin layer — read agent/user memory files.

For Hermes: reads MEMORY.md and USER.md from $HERMES_HOME.
For standalone: reads from configured paths.
"""

from __future__ import annotations

import os
from pathlib import Path


class BuiltinLayer:
    """Read-only access to agent/user memory files (MEMORY.md, USER.md style)."""

    def __init__(
        self,
        memory_path: str | Path | None = None,
        user_path: str | Path | None = None,
    ):
        if memory_path is None:
            hermes_home = os.environ.get("HERMES_HOME", "~/.hermes")
            memory_path = Path(hermes_home).expanduser() / "MEMORY.md"
        if user_path is None:
            hermes_home = os.environ.get("HERMES_HOME", "~/.hermes")
            user_path = Path(hermes_home).expanduser() / "USER.md"
        self.memory_path = Path(memory_path).expanduser()
        self.user_path = Path(user_path).expanduser()

    @property
    def available(self) -> bool:
        return self.memory_path.exists() or self.user_path.exists()

    def read_memory(self) -> str:
        if self.memory_path.exists():
            return self.memory_path.read_text(encoding="utf-8", errors="replace")
        return ""

    def read_user(self) -> str:
        if self.user_path.exists():
            return self.user_path.read_text(encoding="utf-8", errors="replace")
        return ""

    def search(self, query: str) -> list[dict]:
        """Simple substring grep across both files."""
        out = []
        q = query.lower()
        for label, path in (("memory", self.memory_path), ("user", self.user_path)):
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(content.split("\n"), 1):
                if q in line.lower():
                    out.append(
                        {
                            "source": label,
                            "line": i,
                            "content": line.strip(),
                            "score": 0.5,
                        }
                    )
        return out

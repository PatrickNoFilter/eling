"""Code layer — codegraph integration for code symbol memory.

Tries to import codegraph as a library. If unavailable, falls back to
subprocess CLI call. Both paths return the same dict structure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

try:
    from codegraph.core import CodeGraph  # type: ignore
    _HAS_LIB = True
except ImportError:
    _HAS_LIB = False


class CodeLayer:
    """codegraph integration. Library-first, CLI fallback."""

    def __init__(self, project_path: str | Path | None = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self._cli_available = shutil.which("codegraph") is not None
        self._lib_available = _HAS_LIB

    @property
    def available(self) -> bool:
        return self._lib_available or self._cli_available

    def search(self, query: str, max_files: int = 12) -> list[dict]:
        """Symbol search across codebase. Returns list of {file, symbol, kind, line}."""
        if not self.available:
            return []
        if self._lib_available:
            return self._search_lib(query, max_files)
        return self._search_cli(query, max_files)

    def explore(self, query: str, max_files: int = 12) -> dict:
        """Explore a code area — returns symbols + source snippets."""
        if not self.available:
            return {"available": False, "results": []}
        if self._cli_available:
            try:
                out = subprocess.run(
                    ["codegraph", "explore", query, "--max-files", str(max_files), "--json"],
                    cwd=str(self.project_path),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return {"available": True, "results": json.loads(out.stdout)}
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                pass
        return {"available": self.available, "results": []}

    def reindex(self, file_path: str | Path) -> bool:
        """Re-index a specific file in codegraph (no-op if CLI not available)."""
        if not self._cli_available:
            return False
        try:
            subprocess.run(
                ["codegraph", "index", str(file_path)],
                cwd=str(self.project_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _search_lib(self, query: str, max_files: int) -> list[dict]:
        try:
            cg = CodeGraph(str(self.project_path))
            results = cg.search(query, limit=max_files)
            return [{"file": r.file, "symbol": r.name, "kind": r.kind} for r in results]
        except Exception:
            return []

    def _search_cli(self, query: str, max_files: int) -> list[dict]:
        try:
            out = subprocess.run(
                ["codegraph", "search", query, "--limit", str(max_files), "--json"],
                cwd=str(self.project_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                data = json.loads(out.stdout)
                return data if isinstance(data, list) else data.get("results", [])
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return []

"""Code layer — embedded code intelligence (pure Python, no external deps).

Replaced external codegraph (Node.js CLI) with internal AST-based engine.
Zero external dependencies — just Python stdlib.
"""

from __future__ import annotations

from pathlib import Path

from .code_index import CodeIndex


class CodeLayer:
    """Embedded code intelligence — symbol index, search, explore.

    Uses Python AST (for .py files) and regex (for other languages).
    No external tools required.
    """

    def __init__(
        self,
        project_path: str | Path | None = None,
        cache_path: str | Path | None = None,
        auto_index: bool = True,
    ):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        if cache_path is None:
            cache_path = self.project_path / ".eling" / "code_index.json"
        self._index = CodeIndex(cache_path=cache_path)
        self._initialized = False

        if auto_index:
            self._init_index()

    def _init_index(self) -> None:
        """Lazy-load or build index on first use."""
        if self._initialized:
            return
        self._index.load_or_build(self.project_path)
        self._initialized = True

    @property
    def available(self) -> bool:
        # Don't trigger lazy init — just report current state
        if not self._initialized:
            return True  # always available (may be empty)
        return self._index.available or True

    @property
    def symbol_count(self) -> int:
        if not self._initialized:
            return 0
        return self._index.symbol_count

    @property
    def file_count(self) -> int:
        if not self._initialized:
            return 0
        return self._index.file_count

    def search(self, query: str, max_files: int = 12) -> list[dict]:
        """Symbol search across codebase.

        Returns list of {file, symbol, kind, line, column}.
        """
        self._init_index()
        return self._index.search(query, limit=max_files)

    def explore(self, query: str, max_files: int = 12) -> dict:
        """Explore a code area — returns symbols + source snippets.

        Returns {available: bool, results: [{file, symbols, source}]}
        """
        self._init_index()
        return self._index.explore(query, max_files=max_files)

    def reindex(self, file_path: str | Path) -> bool:
        """Re-index a specific file (called when file changes)."""
        self._init_index()
        return self._index.reindex_file(file_path)

    def build_index(self) -> int:
        """Force a full rebuild of the code index.

        Returns number of files indexed.
        """
        self._index.build(self.project_path)
        self._initialized = True
        return self._index.file_count

"""Pure-Python code indexer — embedded code intelligence.

Replaces external codegraph (Node.js CLI) with an internal AST-based engine.
Supports Python (via ast) and generic regex fallback for other languages.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Patterns for quick symbol extraction (non-Python files)
_RE_PATTERNS: list[tuple[str, str]] = [
    # Python (fallback when ast fails)
    ("function", r"(?:^|\n)\s*(?:async\s+)?def\s+(\w+)\s*\("),
    ("method", r"(?:^|\n)\s*(?:async\s+)?def\s+(\w+)\s*\("),
    ("class", r"(?:^|\n)\s*class\s+(\w+)\s*(?:\(|:)"),
    # TypeScript / JavaScript
    ("function", r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("),
    (
        "method",
        r"(?:^|\n)\s*(\w+)\s*(?:=\s*(?:async\s+)?\([^)]*\)\s*=>|\([^)]*\)\s*\{)",
    ),
    ("class", r"(?:^|\n)\s*(?:export\s+)?class\s+(\w+)"),
    ("interface", r"(?:^|\n)\s*(?:export\s+)?interface\s+(\w+)"),
    ("type", r"(?:^|\n)\s*(?:export\s+)?type\s+(\w+)"),
    # Rust
    ("function", r"(?:^|\n)\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
    ("struct", r"(?:^|\n)\s*(?:pub\s+)?struct\s+(\w+)"),
    ("impl", r"(?:^|\n)\s*(?:pub\s+)?impl\s+(\w+)"),
    ("enum", r"(?:^|\n)\s*(?:pub\s+)?enum\s+(\w+)"),
    ("trait", r"(?:^|\n)\s*(?:pub\s+)?trait\s+(\w+)"),
    # Go
    ("function", r"(?:^|\n)\s*func\s+(\w+)\s*\("),
    ("type", r"(?:^|\n)\s*type\s+(\w+)\s"),
    # Ruby
    ("method", r"(?:^|\n)\s*def\s+(?:self\.)?(\w+)"),
    ("class", r"(?:^|\n)\s*class\s+(\w+)"),
    ("module", r"(?:^|\n)\s*module\s+(\w+)"),
    # Java / Kotlin
    ("class", r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?class\s+(\w+)"),
    (
        "interface",
        r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?interface\s+(\w+)",
    ),
    ("method", r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?\w+\s+(\w+)\s*\("),
    # C / C++
    ("function", r"(?:^|\n)\s*\w+\s+(\w+)\s*\([^)]*\)\s*\{"),
    ("class", r"(?:^|\n)\s*class\s+(\w+)"),
    ("struct", r"(?:^|\n)\s*struct\s+(\w+)"),
]

# File extensions treated as code
_CODE_EXTS = {
    ".py",
    ".pyw",
    ".pyx",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".rs",
    ".go",
    ".rb",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".swift",
    ".scala",
    ".ex",
    ".exs",
}

# Directories to skip
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
    "target",
    "build",
    "dist",
    ".egg-info",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hermes",
}

# File size limit (bytes) — skip files larger than this
_MAX_FILE_BYTES = 512 * 1024  # 512 KB


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------


class CodeSymbol:
    """A single symbol extracted from source code."""

    __slots__ = ("file", "symbol", "kind", "line", "column", "source")

    def __init__(
        self,
        file: str,
        symbol: str,
        kind: str,
        line: int,
        column: int = 0,
        source: str = "",
    ):
        self.file = file
        self.symbol = symbol
        self.kind = kind
        self.line = line
        self.column = column
        self.source = source

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "kind": self.kind,
            "line": self.line,
            "column": self.column,
        }


# ---------------------------------------------------------------------------
# AST extraction (Python only)
# ---------------------------------------------------------------------------


def _extract_python_ast(file_path: Path, source: str) -> list[CodeSymbol]:
    """Extract symbols from a Python file using AST."""
    symbols: list[CodeSymbol] = []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(
                CodeSymbol(
                    file=str(file_path),
                    symbol=node.name,
                    kind="class",
                    line=node.lineno,
                    column=node.col_offset,
                )
            )
            # Methods inside class
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef):
                    symbols.append(
                        CodeSymbol(
                            file=str(file_path),
                            symbol=child.name,
                            kind="method",
                            line=child.lineno,
                            column=child.col_offset,
                        )
                    )
        elif isinstance(node, ast.FunctionDef):
            symbols.append(
                CodeSymbol(
                    file=str(file_path),
                    symbol=node.name,
                    kind="function",
                    line=node.lineno,
                    column=node.col_offset,
                )
            )
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append(
                CodeSymbol(
                    file=str(file_path),
                    symbol=node.name,
                    kind="function",
                    line=node.lineno,
                    column=node.col_offset,
                )
            )
    return symbols


# ---------------------------------------------------------------------------
# Regex extraction (fallback for non-Python files)
# ---------------------------------------------------------------------------


def _extract_regex(file_path: Path, source: str) -> list[CodeSymbol]:
    """Extract symbols using regex patterns."""
    symbols: list[CodeSymbol] = []
    seen: set[tuple[int, str]] = set()

    for kind, pattern in _RE_PATTERNS:
        for m in re.finditer(pattern, source):
            name = m.group(1)
            line_num = source[: m.start()].count("\n") + 1
            key = (line_num, name)
            if key not in seen:
                seen.add(key)
                symbols.append(
                    CodeSymbol(
                        file=str(file_path),
                        symbol=name,
                        kind=kind,
                        line=line_num,
                    )
                )
    return symbols


# ---------------------------------------------------------------------------
# File scanner
# ---------------------------------------------------------------------------


def _scan_file(file_path: Path) -> list[CodeSymbol]:
    """Extract symbols from a single file."""
    try:
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            return []
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    ext = file_path.suffix.lower()

    if ext == ".py":
        symbols = _extract_python_ast(file_path, source)
        # If AST extraction fails or returns empty, try regex
        if not symbols:
            symbols = _extract_regex(file_path, source)
    else:
        symbols = _extract_regex(file_path, source)

    return symbols


def _iter_code_files(root: Path) -> Iterator[Path]:
    """Yield all code files under root, skipping ignored dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            path = Path(dirpath) / fn
            if path.suffix.lower() in _CODE_EXTS:
                yield path


# ---------------------------------------------------------------------------
# CodeIndex
# ---------------------------------------------------------------------------


class CodeIndex:
    """In-memory code symbol index with optional persistent cache.

    Usage:
        idx = CodeIndex()
        idx.build("/path/to/project")   # full scan
        results = idx.search("AuthService")
    """

    def __init__(self, cache_path: str | Path | None = None):
        self.cache_path = Path(cache_path) if cache_path else None
        self._symbols: dict[str, list[CodeSymbol]] = {}  # name → symbols
        self._file_index: dict[str, list[CodeSymbol]] = {}  # file → symbols
        self._built_at: float = 0.0
        self._loaded = False

    # -- Properties ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._loaded and bool(self._symbols)

    @property
    def symbol_count(self) -> int:
        return sum(len(v) for v in self._symbols.values())

    @property
    def file_count(self) -> int:
        return len(self._file_index)

    @property
    def stale(self) -> bool:
        """True if index is old (>5 min) and should be rebuilt."""
        if not self._built_at:
            return True
        return (time.time() - self._built_at) > 300

    # -- Build / Rebuild ----------------------------------------------------

    def build(self, root_path: str | Path) -> int:
        """Scan a project directory and build the index.

        Returns number of files indexed.
        """
        root = Path(root_path).resolve()
        self._symbols.clear()
        self._file_index.clear()

        indexed = 0
        for file_path in _iter_code_files(root):
            try:
                symbols = _scan_file(file_path)
            except Exception:
                continue
            if not symbols:
                continue

            # Deduplicate by (line, name) for safety
            seen: set[tuple[int, str]] = set()
            uniq: list[CodeSymbol] = []
            for s in symbols:
                key = (s.line, s.symbol)
                if key not in seen:
                    seen.add(key)
                    uniq.append(s)

            fpath = str(file_path)
            self._file_index[fpath] = uniq
            for s in uniq:
                self._symbols.setdefault(s.symbol, []).append(s)
            indexed += 1

        self._built_at = time.time()
        self._loaded = True
        self._save_cache()
        return indexed

    def reindex_file(self, file_path: str | Path) -> bool:
        """Re-index a single file. Returns True if symbols changed."""
        path = Path(file_path)
        if not path.exists() or path.suffix.lower() not in _CODE_EXTS:
            return False

        # Remove old entries for this file
        fpath = str(path)
        old_symbols = self._file_index.pop(fpath, [])
        for s in old_symbols:
            name = s.symbol
            if name in self._symbols:
                self._symbols[name] = [
                    x for x in self._symbols[name] if x.file != fpath
                ]
                if not self._symbols[name]:
                    del self._symbols[name]

        symbols = _scan_file(path)
        if symbols:
            self._file_index[fpath] = symbols
            for s in symbols:
                self._symbols.setdefault(s.symbol, []).append(s)

        self._save_cache()
        return True

    # -- Search -------------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search symbols by name (case-insensitive substring).

        Returns [{file, symbol, kind, line, column}].
        """
        if not self._loaded:
            return []

        q = query.lower()
        results: list[CodeSymbol] = []
        for name, symbols in self._symbols.items():
            if q in name.lower():
                results.extend(symbols)

        # Sort: exact match first, then prefix, then substring
        def _sort_key(s: CodeSymbol) -> tuple:
            name_lower = s.symbol.lower()
            return (
                0 if name_lower == q else 1 if name_lower.startswith(q) else 2,
                len(s.source),
                s.line,
            )

        results.sort(key=_sort_key)
        return [s.to_dict() for s in results[:limit]]

    def explore(self, query: str, max_files: int = 12) -> dict:
        """Explore code — search + fetch source snippet for each result.

        Returns {available: bool, results: [{file, symbols, source}]}
        """
        raw = self.search(query, limit=50)
        if not raw:
            return {"available": self._loaded, "results": []}

        # Group by file
        by_file: dict[str, list[dict]] = {}
        for r in raw:
            by_file.setdefault(r["file"], []).append(r)

        # Fetch source for each file
        out = []
        for file_path in list(by_file.keys())[:max_files]:
            path = Path(file_path)
            snippet = ""
            if path.exists():
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                    lines = source.splitlines()
                    symbols_for_file = by_file[file_path]
                    # Grab context around first symbol
                    first_line = max(0, symbols_for_file[0]["line"] - 3)
                    last_line = min(len(lines), symbols_for_file[0]["line"] + 8)
                    snippet = "\n".join(lines[first_line:last_line])
                except OSError:
                    logger.debug(
                        "code symbol source read failed (non-fatal): %s", file_path
                    )

            out.append(
                {
                    "file": file_path,
                    "symbols": by_file[file_path],
                    "snippet": snippet,
                }
            )

        return {"available": self._loaded, "results": out}

    # -- Cache persistence --------------------------------------------------

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            data = {
                "built_at": self._built_at,
                "files": {
                    fpath: [s.to_dict() | {"source": s.source} for s in syms]
                    for fpath, syms in self._file_index.items()
                },
            }
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(data, indent=2))
        except OSError:
            logger.debug(
                "code index cache write failed (non-fatal): %s", self.cache_path
            )

    def _load_cache(self) -> int:
        """Load from cache file. Returns number of files loaded."""
        if not self.cache_path or not self.cache_path.exists():
            return 0
        try:
            data = json.loads(self.cache_path.read_text())
            self._built_at = data.get("built_at", 0)
            for fpath, syms_data in data.get("files", {}).items():
                symbols = [
                    CodeSymbol(
                        file=s["file"],
                        symbol=s["symbol"],
                        kind=s["kind"],
                        line=s["line"],
                        column=s.get("column", 0),
                        source=s.get("source", ""),
                    )
                    for s in syms_data
                ]
                self._file_index[fpath] = symbols
                for s in symbols:
                    self._symbols.setdefault(s.symbol, []).append(s)
            self._loaded = True
            return len(self._file_index)
        except (json.JSONDecodeError, OSError, KeyError):
            return 0

    def load_or_build(self, root_path: str | Path) -> int:
        """Try loading from cache; fall back to full build.

        Returns number of files indexed.
        """
        count = self._load_cache()
        if count > 0:
            return count
        return self.build(root_path)

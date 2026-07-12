"""Obsidian layer — file-based local Markdown vault for human-readable notes.

Layer 6 in Eling's cognitive stack. Provides a filesystem-first note layer
that mirrors select facts and KB entries as readable Markdown files in an
Obsidian vault. Respects Obsidian conventions: frontmatter, wiki links,
daily notes, and folder structure.

Use instead of (or alongside) Notion Layer 7 when you want
local-first, Git-friendly, private, portable Markdown notes.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import typing as _t
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Default vault paths ────────────────────────────────────────────

DEFAULT_VAULT_SUBDIR = "Hermes-Agent"
DEFAULT_FOLDERS = {
    "Projects",
    "Daily",
    "Research",
    "Memory-Review",
    "Skills-Notes",
}


class ObsidianLayer:
    """Read/write/search a local Obsidian vault via the filesystem.

    Parameters
    ----------
    vault_path : str or Path, optional
        Absolute path to the Obsidian vault root.
        Falls back to ``OBSIDIAN_VAULT_PATH`` env var, then
        ``~/Documents/Obsidian/<DEFAULT_VAULT_SUBDIR>/``.
    auto_create_folders : bool
        If True, create the default subfolder structure on init.
    """

    def __init__(
        self,
        vault_path: str | Path | None = None,
        auto_create_folders: bool = True,
    ):
        # Resolve vault root
        if vault_path:
            self._vault = Path(vault_path).expanduser().resolve()
        elif env_path := os.environ.get("OBSIDIAN_VAULT_PATH"):
            self._vault = Path(env_path).expanduser().resolve()
        else:
            self._vault = (
                Path.home() / "Documents" / "Obsidian" / DEFAULT_VAULT_SUBDIR
            ).resolve()

        self._vault.mkdir(parents=True, exist_ok=True)

        if auto_create_folders:
            for folder in DEFAULT_FOLDERS:
                (self._vault / folder).mkdir(parents=True, exist_ok=True)

    # ── Public properties ───────────────────────────────────────────

    @property
    def vault_path(self) -> Path:
        """Absolute path to the vault root directory."""
        return self._vault

    @property
    def available(self) -> bool:
        """True if the vault directory exists and is writable."""
        return self._vault.is_dir() and os.access(str(self._vault), os.W_OK)

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict[str, _t.Any]]:
        """Search vault Markdown files by content (simple grep).

        Returns up to ``limit`` results with path, title, and snippet.
        """
        if not self.available:
            return []

        results: list[dict[str, _t.Any]] = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        for md_file in self._vault.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            matches = list(pattern.finditer(text))
            if not matches:
                continue

            # Build a snippet around the first match
            first = matches[0]
            start = max(0, first.start() - 60)
            end = min(len(text), first.end() + 60)
            snippet = text[start:end].replace("\n", " ").strip()

            results.append(
                {
                    "path": str(md_file.relative_to(self._vault)),
                    "title": self._title_from_path(md_file),
                    "snippet": snippet,
                    "matches": len(matches),
                }
            )

            if len(results) >= limit:
                break

        return results

    # ── Read ────────────────────────────────────────────────────────

    def read(self, path: str) -> str | None:
        """Read a Markdown file relative to the vault root.

        Returns None if the file doesn't exist or isn't a Markdown file.
        """
        full = self._resolve(path)
        if not full or not full.is_file():
            return None
        try:
            return full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def list_files(self, folder: str = "") -> list[str]:
        """List Markdown files, optionally scoped to a subfolder."""
        search_dir = self._vault / folder if folder else self._vault
        if not search_dir.is_dir():
            return []
        return sorted(str(p.relative_to(self._vault)) for p in search_dir.rglob("*.md"))

    # ── Write ───────────────────────────────────────────────────────

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, _t.Any] | None = None,
    ) -> str | None:
        """Write a Markdown file, creating parent dirs as needed.

        Returns the absolute path written, or None on failure.

        Parameters
        ----------
        path : str
            Relative path under vault root (e.g. ``Projects/my-project.md``).
        content : str
            Markdown body.
        frontmatter : dict, optional
            Optional YAML frontmatter dict (converted to ``key: value`` lines).
        """
        if not path.endswith(".md"):
            path += ".md"

        full = self._vault / path
        full.parent.mkdir(parents=True, exist_ok=True)

        parts: list[str] = []

        if frontmatter:
            parts.append("---")
            for k, v in frontmatter.items():
                parts.append(f"{k}: {v}")
            parts.append("---")
            parts.append("")

        parts.append(content)

        try:
            full.write_text("\n".join(parts), encoding="utf-8")
            return str(full)
        except OSError as e:
            logger.warning("Obsidian write failed: %s", e)
            return None

    # ── Daily notes ─────────────────────────────────────────────────

    def daily_note(
        self,
        content: str = "",
        date: datetime.date | None = None,
    ) -> str | None:
        """Create or update a daily note at ``Daily/YYYY-MM-DD.md``.

        If the file already exists and content is empty, returns its path
        without modification. If content is non-empty, appends it below an
        ``## `` timestamped heading.
        """
        date = date or datetime.date.today()
        path = f"Daily/{date.isoformat()}.md"

        existing = self.read(path)
        if existing and not content:
            return str(self._vault / path)

        timestamp = datetime.datetime.now().strftime("%H:%M")
        entry = f"\n## {timestamp}\n\n{content}\n"

        if existing:
            full_content = existing.rstrip("\n") + "\n" + entry
        else:
            full_content = (
                f"# Daily — {date.isoformat()}\n\n_Created by Eling v0.11.0_\n\n{entry}"
            )

        return self.write(path, full_content)

    # ── Sync helpers ───────────────────────────────────────────────

    def sync_fact(
        self,
        fact_id: int,
        content: str,
        category: str = "general",
    ) -> str | None:
        """Sync a single fact as a note in ``Memory-Review/<category>.md``.

        Appends a dated entry so the vault accumulates a reviewable trail.
        """
        path = f"Memory-Review/{category}.md"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"### Fact #{fact_id} ({timestamp})\n\n{content}\n\n---\n"

        existing = self.read(path) or ""
        full_content = existing + "\n" + entry
        return self.write(path, full_content)

    # ── Internals ──────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path | None:
        """Resolve a vault-relative path, enforcing it stays under vault."""
        if not path.endswith(".md"):
            path += ".md"
        full = (self._vault / path).resolve()
        # Safety: prevent path traversal outside vault
        try:
            full.relative_to(self._vault)
        except ValueError:
            logger.warning("Path traversal blocked: %s", path)
            return None
        return full

    @staticmethod
    def _title_from_path(md_path: Path) -> str:
        """Extract a readable title from the file path."""
        stem = md_path.stem
        # Check for first # heading
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if match := re.search(r"^# (.+)", text, re.MULTILINE):
                return match.group(1).strip()
        except Exception:
            pass
        return stem.replace("-", " ").replace("_", " ").title()


# ── Convenience helpers ───────────────────────────────────────────


def _default_vault_path() -> Path:
    """Return the default vault path without instantiating the layer."""
    env = os.environ.get("OBSIDIAN_VAULT_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / "Documents" / "Obsidian" / DEFAULT_VAULT_SUBDIR

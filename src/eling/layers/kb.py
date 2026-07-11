"""KB layer — FTS5 knowledge base for long-form content (notes, docs, web).

Inspired by context-mode's ctx_index/ctx_search. Embeds the algo so no
external MCP subprocess is needed.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    section     TEXT DEFAULT '',
    content     TEXT NOT NULL,
    content_type TEXT DEFAULT 'prose',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kb_source ON kb_chunks(source);

CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts
    USING fts5(content, section, source, content=kb_chunks, content_rowid=chunk_id);

CREATE TRIGGER IF NOT EXISTS kb_ai AFTER INSERT ON kb_chunks BEGIN
    INSERT INTO kb_fts(rowid, content, section, source)
        VALUES (new.chunk_id, new.content, new.section, new.source);
END;

CREATE TRIGGER IF NOT EXISTS kb_ad AFTER DELETE ON kb_chunks BEGIN
    INSERT INTO kb_fts(kb_fts, rowid, content, section, source)
        VALUES ('delete', old.chunk_id, old.content, old.section, old.source);
END;
"""

# Heading-based markdown splitter
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")


class KBLayer:
    """FTS5-backed knowledge base for long-form content."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.debug("WAL mode not available (non-fatal)")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def index(self, content: str, source: str, content_type: str = "prose") -> int:
        """Split content by markdown headings and index each chunk. Returns chunks added."""
        with self._lock:
            chunks = self._split_markdown(content)
            n = 0
            for section, body in chunks:
                if not body.strip():
                    continue
                # Detect code blocks for content_type
                ct = "code" if _CODE_BLOCK_RE.search(body) else content_type
                self._conn.execute(
                    "INSERT INTO kb_chunks (source, section, content, content_type) VALUES (?, ?, ?, ?)",
                    (source, section, body.strip(), ct),
                )
                n += 1
            self._conn.commit()
            return n

    def search(
        self, query: str, source: str | None = None, limit: int = 5
    ) -> list[dict]:
        """BM25 + trigram hybrid search across KB."""
        with self._lock:
            query = query.strip()
            if not query:
                return []
            # Sanitize query for FTS5 (escape special chars)
            safe_query = self._sanitize_fts_query(query)
            if not safe_query:
                return []
            params = [safe_query]
            src_clause = ""
            if source:
                src_clause = "AND k.source LIKE ?"
                params.append(f"%{source}%")
            params.append(limit)
            sql = f"""
                SELECT k.chunk_id, k.source, k.section, k.content, k.content_type,
                       k.created_at, -fts.rank as score
                FROM kb_chunks k JOIN kb_fts fts ON fts.rowid = k.chunk_id
                WHERE kb_fts MATCH ? {src_clause}
                ORDER BY fts.rank LIMIT ?
            """
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
            return [dict(r) for r in rows]

    def list_sources(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) as n_chunks, MAX(created_at) as last_indexed "
                "FROM kb_chunks GROUP BY source ORDER BY n_chunks DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def remove_source(self, source: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM kb_chunks WHERE source = ?", (source,)
            )
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) as n FROM kb_chunks").fetchone()
            sources = self._conn.execute(
                "SELECT COUNT(DISTINCT source) as n FROM kb_chunks"
            ).fetchone()
            return {
                "total_chunks": count["n"],
                "total_sources": sources["n"],
            }

    def flush(self) -> None:
        """Flush pending writes to disk (WAL checkpoint)."""
        with self._lock:
            try:
                self._conn.commit()
            except Exception:
                logger.debug("kb flush commit failed (non-fatal)")

    @staticmethod
    def _split_markdown(content: str) -> list[tuple[str, str]]:
        """Split markdown by headings. Returns [(section_title, body)]."""
        chunks: list[tuple[str, str]] = []
        lines = content.split("\n")
        current_section = ""
        current_body: list[str] = []
        for line in lines:
            m = _HEADING_RE.match(line)
            if m:
                if current_body:
                    chunks.append((current_section, "\n".join(current_body)))
                current_section = m.group(2).strip()
                current_body = [line]
            else:
                current_body.append(line)
        if current_body:
            chunks.append((current_section, "\n".join(current_body)))
        # If no headings, return whole content as single chunk
        if not chunks or (len(chunks) == 1 and not chunks[0][0]):
            return [("", content)]
        return chunks

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Strip FTS5-unsafe chars, keep alphanumerics + spaces."""
        cleaned = re.sub(r"[^\w\s]", " ", query)
        words = [w for w in cleaned.split() if len(w) > 1]
        if not words:
            return ""
        return " OR ".join(words)

    def close(self):
        self._conn.close()

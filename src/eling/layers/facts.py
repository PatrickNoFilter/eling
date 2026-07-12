"""Facts layer — SQLite-backed fact store with HRR + BM25 + Jaccard hybrid retrieval.

Adapted from the holographic memory plugin by dusterbloom (Hermes PR #2351, MIT).
Standalone — accepts any db_path, no Hermes dependencies.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import logging

from . import hrr
from .embeddings import EmbeddingIndex
from .. import decay

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source          TEXT DEFAULT 'facts',
    notion_page_id  TEXT,
    hrr_vector      BLOB
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    entity_type TEXT DEFAULT 'unknown',
    aliases     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_entities (
    fact_id   INTEGER REFERENCES facts(fact_id) ON DELETE CASCADE,
    entity_id INTEGER REFERENCES entities(entity_id),
    PRIMARY KEY (fact_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_facts_trust    ON facts(trust_score DESC);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_source   ON facts(source);
CREATE INDEX IF NOT EXISTS idx_entities_name  ON entities(name);

CREATE TABLE IF NOT EXISTS entity_graph (
    entity_a_id INTEGER NOT NULL REFERENCES entities(entity_id),
    entity_b_id INTEGER NOT NULL REFERENCES entities(entity_id),
    weight      REAL DEFAULT 1.0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_a_id, entity_b_id)
);

CREATE TABLE IF NOT EXISTS fact_links (
    fact_id_a   INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    fact_id_b   INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    weight      REAL DEFAULT 1.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fact_id_a, fact_id_b)
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, tags, content=facts, content_rowid=fact_id);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
"""

_HELPFUL_DELTA = 0.05
_UNHELPFUL_DELTA = -0.10

_RE_CAPITALIZED = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_RE_DOUBLE_QUOTE = re.compile(r'"([^"]+)"')
_RE_SINGLE_QUOTE = re.compile(r"'([^']+)'")
_RE_WIKI_LINK = re.compile(r"\[\[([^\]]+)\]\]")

# ── Temporal / Date parsing ───────────────────────────────────────────────
_RELATIVE_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    (re.compile(r"(?i)\b(lately|recently|baru[- ]?baru ini)\b"), "day", -7),
    (re.compile(r"(?i)\b(yesterday|kemarin)\b"), "day", -1),
    (re.compile(r"(?i)\b(today|hari ini|sekarang)\b"), "day", 0),
    (re.compile(r"(?i)\b(this\s+week|minggu ini)\b"), "week", 0),
    (re.compile(r"(?i)\b(last\s+week|minggu lalu)\b"), "week", -1),
    (re.compile(r"(?i)\b(this\s+month|bulan ini)\b"), "month", 0),
    (re.compile(r"(?i)\b(last\s+month|bulan lalu)\b"), "month", -1),
    (re.compile(r"(?i)\b(tomor?row|besok)\b"), "day", 1),
    (re.compile(r"(?i)\b(this\s+(year|quarter))\b"), "year", 0),
    (re.compile(r"(?i)\b(last\s+(year|quarter|3\s*months))\b"), "year", -1),
    # Numbered relative: "last X days/weeks/months"
    (
        re.compile(
            r"(?i)(?:last|past|previous)\s+(\d+)\s*(days?|hours?|h|minutes?|menit|jam)\b"
        ),
        "num",
        0,
    ),
    (
        re.compile(
            r"(?i)(?:next|coming)\s+(\d+)\s*(days?|hours?|h|minutes?|menit|jam)\b"
        ),
        "num",
        1,
    ),
]
"""Patterns for relative time expressions. Each entry: (pattern, unit, offset_direction)."""

_ABSOLUTE_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})[/](\d{1,2})[/](\d{4})\b"), "us"),
    (
        re.compile(
            r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
            re.I,
        ),
        "named",
    ),
]
"""Absolute date patterns: ISO 8601, US-style, named-month."""

_TEMPORAL_INTENT_KEYWORDS = frozenset(
    "yesterday today tomorrow kemarin besok lately recently "
    "this week last week this month last month this year last year "
    "last past previous next coming since from after before "
    "between range 202 2025 2026 2027 2028 2029 2030".split()
)

_VERSIONING_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    tags        TEXT DEFAULT '',
    trust_score REAL DEFAULT 0.5,
    source      TEXT DEFAULT 'facts',
    action      TEXT DEFAULT 'created',
    reason      TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fact_versions_fact_id ON fact_versions(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_versions_created_at ON fact_versions(created_at);
"""

# ── contradiction / consistency ──
CONTRADICTION_THRESHOLD = 0.3
"""Jaccard similarity below this (with overlapping entities) → flag as contradiction."""


def _tag_has(tags: str, flag: str) -> bool:
    """Check if a comma-separated tags string contains *flag*."""
    return flag in tags.split(",")


def _tag_add(tags: str, flag: str) -> str:
    """Append *flag* to a comma-separated tags string if not already present."""
    if _tag_has(tags, flag):
        return tags
    return (tags + "," + flag) if tags else flag


def _tag_remove(tags: str, flag: str) -> str:
    """Remove *flag* from a comma-separated tags string."""
    parts = [t for t in tags.split(",") if t and t != flag]
    return ",".join(parts)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class FactsLayer:
    """SQLite-backed fact store with HRR + BM25 + Jaccard hybrid retrieval."""

    def __init__(
        self,
        db_path: str | Path,
        default_trust: float = 0.5,
        hrr_dim: int = 1024,
        fts_weight: float = 0.4,
        jaccard_weight: float = 0.3,
        hrr_weight: float = 0.3,
        embedding_model: str = "",
    ):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_trust = _clamp(default_trust)
        self.hrr_dim = hrr_dim
        self.embedding_model = embedding_model
        # Probe numpy availability eagerly — _HAS_NUMPY is None until
        # _require_numpy() is called, and not None is True, so we must
        # resolve it here.
        try:
            import numpy  # noqa: F401

            hrr._HAS_NUMPY = True
        except ImportError:
            hrr._HAS_NUMPY = False
        self._hrr_available = hrr._HAS_NUMPY

        if hrr_weight > 0 and not self._hrr_available:
            fts_weight, jaccard_weight, hrr_weight = 0.6, 0.4, 0.0
        self.fts_weight = fts_weight
        self.jaccard_weight = jaccard_weight
        self.hrr_weight = hrr_weight

        # Optional embedding index
        self.embedding_index: EmbeddingIndex | None = None
        if embedding_model:
            try:
                self.embedding_index = EmbeddingIndex(
                    self.db_path, model_name=embedding_model
                )
            except Exception as e:
                logger.debug("Embedding index not available: %s", e)

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
        self._conn.executescript(_VERSIONING_SCHEMA)
        # Migration: add strength + last_access_at columns (v4 forgetting engine)
        # NOTE: SQLite ALTER TABLE only allows constant default values, not
        # CURRENT_TIMESTAMP, so last_access_at starts as NULL.
        for col in (
            "ALTER TABLE facts ADD COLUMN strength REAL DEFAULT 1.0",
            "ALTER TABLE facts ADD COLUMN last_access_at TIMESTAMP",
        ):
            try:
                self._conn.execute(col)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    # ---- write ops ----
    def add(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
        source: str = "facts",
    ) -> int:
        with self._lock:
            content = content.strip()
            if not content:
                raise ValueError("content must not be empty")
            try:
                cur = self._conn.execute(
                    "INSERT INTO facts (content, category, tags, trust_score, source, strength, last_access_at) VALUES (?, ?, ?, ?, ?, 1.0, CURRENT_TIMESTAMP)",
                    (content, category, tags, self.default_trust, source),
                )
                self._conn.commit()
                fact_id = cur.lastrowid
                # Track initial version
                self._conn.execute(
                    "INSERT INTO fact_versions (fact_id, content, category, tags, trust_score, source, action) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'created')",
                    (fact_id, content, category, tags, self.default_trust, source),
                )
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT fact_id FROM facts WHERE content = ?", (content,)
                ).fetchone()
                return int(row["fact_id"])

            for name in self._extract_entities(content):
                eid = self._resolve_entity(name)
                self._conn.execute(
                    "INSERT OR IGNORE INTO fact_entities VALUES (?, ?)", (fact_id, eid)
                )
            self._compute_hrr_vector(fact_id, content)
            if self.embedding_index:
                self.embedding_index.index_fact(fact_id, content)

            # Self-wiring graph: edges between co-occurring entities
            try:
                self.self_wire_graph(fact_id)
            except Exception as exc:
                logger.debug("self_wire_graph failed for fact %s: %s", fact_id, exc)
            # Post-write contradiction check
            try:
                self.detect_contradictions(fact_id)
            except Exception as exc:
                logger.debug(
                    "detect_contradictions failed for fact %s: %s", fact_id, exc
                )
            # Zettelkasten memory linking: connect to related facts
            try:
                self.create_links(fact_id)
            except Exception as exc:
                logger.debug("create_links failed for fact %s: %s", fact_id, exc)

            self._conn.commit()
            return int(fact_id)

    def remove(self, fact_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                "DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,)
            )
            self._conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            if self.embedding_index:
                self.embedding_index.remove_fact(fact_id)
            self._conn.commit()
            return True

    def update_trust(self, fact_id: int, helpful: bool) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT trust_score, helpful_count, strength FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"fact_id {fact_id} not found")
            delta = _HELPFUL_DELTA if helpful else _UNHELPFUL_DELTA
            new_trust = _clamp(row["trust_score"] + delta)
            # Boost strength on helpful feedback (decision_made)
            new_strength = decay.boost_strength(
                row["strength"], decay.DECISION_BOOST if helpful else 0.0
            )
            self._conn.execute(
                "UPDATE facts SET trust_score=?, helpful_count=helpful_count+?, "
                "strength=?, last_access_at=CURRENT_TIMESTAMP WHERE fact_id=?",
                (new_trust, 1 if helpful else 0, new_strength, fact_id),
            )
            self._conn.commit()
            return {
                "fact_id": fact_id,
                "trust_score": new_trust,
                "strength": new_strength,
            }

    def set_notion_page(self, fact_id: int, notion_page_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE facts SET notion_page_id = ? WHERE fact_id = ?",
                (notion_page_id, fact_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ---- search ops ----
    def search(
        self,
        query: str,
        category: str | None = None,
        source: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
        include_cleared: bool = False,
    ) -> list[dict]:
        """Hybrid BM25 + Jaccard + HRR search. Filter by category or source.

        Excludes cleared facts (strength <= DORMANT_THRESHOLD) unless include_cleared=True.
        Boosts strength + updates last_access_at for returned facts.
        """
        with self._lock:
            query = query.strip()
            if not query:
                return []
            candidates = self._fts_candidates(
                query, category, source, min_trust, limit * 3
            )
            if not candidates:
                return []
            # Filter out cleared facts unless explicitly included
            if not include_cleared:
                candidates = [
                    c
                    for c in candidates
                    if c.get("strength", 1.0) > decay.DORMANT_THRESHOLD
                ]
                if not candidates:
                    return []
            query_tokens = self._tokenize(query)
            candidate_ids = [c["fact_id"] for c in candidates]
            # Compute embedding scores for all candidates at once
            emb_scores: dict[int, float] = {}
            if self.embedding_index and self.embedding_index.available:
                emb_scores = self.embedding_index.search(query, candidate_ids)
            scored = []
            for fact in candidates:
                content_tokens = self._tokenize(fact["content"])
                tag_tokens = self._tokenize(fact.get("tags") or "")
                jaccard = self._jaccard(query_tokens, content_tokens | tag_tokens)
                fts_score = fact.get("fts_rank", 0.0)
                if self.hrr_weight > 0 and fact.get("hrr_vector"):
                    fact_vec = hrr.bytes_to_phases(fact["hrr_vector"])
                    query_vec = hrr.encode_text(query, self.hrr_dim)
                    hrr_sim = (hrr.similarity(query_vec, fact_vec) + 1.0) / 2.0
                else:
                    hrr_sim = 0.5
                emb_sim = emb_scores.get(fact["fact_id"], 0.5)
                relevance = (
                    self.fts_weight * fts_score
                    + self.jaccard_weight * jaccard
                    + self.hrr_weight * hrr_sim
                    + 0.1 * emb_sim
                )
                fact["score"] = relevance * fact["trust_score"]
                fact.pop("hrr_vector", None)
                scored.append(fact)
            scored.sort(key=lambda x: x["score"], reverse=True)
            results = scored[:limit]
            # Boost strength for returned facts (read/recall boost)
            for fact in results:
                self._boost_strength(fact["fact_id"], decay.READ_BOOST)
            self._conn.commit()
            return results

    _FTS_SPECIAL = frozenset('^*"()+~:.-')

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Escape FTS5 special chars (dots, parens, colons etc.) so queries don't crash FTS5."""
        import shlex

        try:
            tokens = shlex.split(query)
        except ValueError:
            tokens = [query]
        out = []
        for t in tokens:
            if any(c in FactsLayer._FTS_SPECIAL for c in t):
                out.append('"' + t.replace('"', '""') + '"')
            else:
                out.append(t)
        return " ".join(out) or query

    def _fts_candidates(
        self,
        query: str,
        category: str | None,
        source: str | None,
        min_trust: float,
        limit: int,
    ) -> list[dict]:
        safe_query = self._sanitize_fts_query(query)
        params = [safe_query, min_trust]
        filters = []
        if category:
            filters.append("f.category = ?")
            params.append(category)
        if source:
            filters.append("f.source = ?")
            params.append(source)
        filter_clause = " AND ".join(filters) if filters else ""
        if filter_clause:
            filter_clause = " AND " + filter_clause
        params.append(limit)
        sql = f"""
            SELECT f.fact_id, f.content, f.category, f.tags, f.trust_score,
                   f.retrieval_count, f.helpful_count, f.created_at, f.updated_at,
                   f.source, f.notion_page_id, f.hrr_vector, f.strength,
                   -fts.rank as fts_rank
            FROM facts f JOIN facts_fts fts ON fts.rowid = f.fact_id
            WHERE facts_fts MATCH ? AND f.trust_score >= ? {filter_clause}
            ORDER BY fts.rank LIMIT ?
        """
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except (
            sqlite3.OperationalError,
            sqlite3.DatabaseError,
            sqlite3.IntegrityError,
        ):
            return []
        cands = [dict(r) for r in rows]
        if cands:
            max_rank = max(c.get("fts_rank", 0.0) for c in cands) or 1.0
            for c in cands:
                c["fts_rank"] = c.get("fts_rank", 0.0) / max_rank
        return cands

    def list_all(
        self,
        category: str | None = None,
        min_trust: float = 0.0,
        limit: int = 100,
        include_cleared: bool = False,
    ) -> list[dict]:
        with self._lock:
            params = [min_trust]
            cat_clause = ""
            if category:
                cat_clause = "AND category = ?"
                params.append(category)
            cleared_clause = ""
            if not include_cleared:
                cleared_clause = "AND (strength IS NULL OR strength > ?)"
                params.append(decay.DORMANT_THRESHOLD)
            params.append(limit)
            sql = f"""
                SELECT fact_id, content, category, tags, trust_score, retrieval_count,
                       helpful_count, created_at, updated_at, source, notion_page_id,
                       strength
                FROM facts WHERE trust_score >= ? {cat_clause} {cleared_clause}
                ORDER BY trust_score DESC LIMIT ?
            """
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get(self, fact_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, content, category, tags, trust_score, retrieval_count, "
                "helpful_count, created_at, updated_at, source, notion_page_id "
                "FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if row:
                # Update last_access_at on read
                self._conn.execute(
                    "UPDATE facts SET last_access_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                    (fact_id,),
                )
                self._conn.commit()
            return dict(row) if row else None

    def set_trust(self, fact_id: int, score: float) -> None:
        """Update the trust score for a fact (clamped to [0, 1])."""
        score = max(0.0, min(1.0, score))
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET trust_score = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE fact_id = ?",
                (score, fact_id),
            )
            self._conn.commit()

    # ── decay / forgetting engine (v4) ─────────────────────────────────

    def apply_decay(self, decay_rate: float = decay.DEFAULT_DECAY_RATE) -> dict:
        """Apply exponential strength decay to all facts.

        strength *= exp(-decay_rate * days_since_last_access)

        Returns dict with counts of active/dormant/cleared facts after decay.
        """
        with self._lock:
            self._conn.execute(
                """UPDATE facts
                   SET strength = MAX(0.0, MIN(1.0, strength * exp(-? * (julianday('now') - julianday(last_access_at)))))
                   WHERE julianday('now') - julianday(last_access_at) > 0""",
                (decay_rate,),
            )
            self._conn.commit()

            # Count lifecycle states
            total = self._conn.execute("SELECT COUNT(*) as n FROM facts").fetchone()[
                "n"
            ]
            active = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ?",
                (decay.ACTIVE_THRESHOLD,),
            ).fetchone()["n"]
            dormant = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ? AND strength <= ?",
                (decay.DORMANT_THRESHOLD, decay.ACTIVE_THRESHOLD),
            ).fetchone()["n"]
            cleared = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength <= ?",
                (decay.DORMANT_THRESHOLD,),
            ).fetchone()["n"]

        return {
            "total": int(total),
            "active": int(active),
            "dormant": int(dormant),
            "cleared": int(cleared),
        }

    def _boost_strength(self, fact_id: int, boost: float = decay.READ_BOOST) -> None:
        """Apply a strength boost to a fact, clamped to [0, 1]."""
        self._conn.execute(
            """UPDATE facts
               SET strength = MAX(0.0, MIN(1.0, strength + ?)),
                   last_access_at = CURRENT_TIMESTAMP
               WHERE fact_id = ?""",
            (boost, fact_id),
        )

    # ── stats ──────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            counts = self._conn.execute("SELECT COUNT(*) as n FROM facts").fetchone()
            ents = self._conn.execute("SELECT COUNT(*) as n FROM entities").fetchone()
            cats = self._conn.execute(
                "SELECT category, COUNT(*) as n FROM facts GROUP BY category"
            ).fetchall()
            active = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ?",
                (decay.ACTIVE_THRESHOLD,),
            ).fetchone()["n"]
            dormant = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ? AND strength <= ?",
                (decay.DORMANT_THRESHOLD, decay.ACTIVE_THRESHOLD),
            ).fetchone()["n"]
            cleared = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength <= ?",
                (decay.DORMANT_THRESHOLD,),
            ).fetchone()["n"]
            result = {
                "total_facts": counts["n"],
                "total_entities": ents["n"],
                "by_category": {r["category"]: r["n"] for r in cats},
                "hrr_enabled": self._hrr_available,
                "active_facts": int(active),
                "dormant_facts": int(dormant),
                "cleared_facts": int(cleared),
                "pending_contradictions": int(
                    self._conn.execute(
                        "SELECT COUNT(*) as n FROM facts WHERE tags LIKE ?",
                        (f"%{self.CONTRADICTION_FLAG}%",),
                    ).fetchone()["n"]
                ),
            }
            if self.embedding_index:
                result["embeddings"] = self.embedding_index.stats()
            return result

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {t.lower() for t in re.findall(r"\w+", text)}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _extract_entities(self, text: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []

        def _add(n: str):
            n = n.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)

        for m in _RE_WIKI_LINK.finditer(text):
            _add(m.group(1))
        for m in _RE_CAPITALIZED.finditer(text):
            _add(m.group(1))
        for m in _RE_DOUBLE_QUOTE.finditer(text):
            _add(m.group(1))
        for m in _RE_SINGLE_QUOTE.finditer(text):
            _add(m.group(1))
        return out

    def _resolve_entity(self, name: str) -> int:
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name LIKE ?", (name,)
        ).fetchone()
        if row:
            return int(row["entity_id"])
        cur = self._conn.execute("INSERT INTO entities (name) VALUES (?)", (name,))
        return int(cur.lastrowid)

    def _compute_hrr_vector(self, fact_id: int, content: str) -> None:
        if not self._hrr_available:
            return
        rows = self._conn.execute(
            "SELECT e.name FROM entities e JOIN fact_entities fe ON fe.entity_id = e.entity_id "
            "WHERE fe.fact_id = ?",
            (fact_id,),
        ).fetchall()
        entities = [r["name"] for r in rows]
        vector = hrr.encode_fact(content, entities, self.hrr_dim)
        self._conn.execute(
            "UPDATE facts SET hrr_vector = ? WHERE fact_id = ?",
            (hrr.phases_to_bytes(vector), fact_id),
        )

    def probe(
        self, entity: str, limit: int = 10, include_cleared: bool = False
    ) -> list[dict]:
        """Find facts mentioning a single entity.

        Excludes cleared facts (strength <= DORMANT_THRESHOLD) unless include_cleared=True.
        """
        with self._lock:
            cleared_clause = ""
            params: list = [entity]
            if not include_cleared:
                cleared_clause = "AND (f.strength IS NULL OR f.strength > ?)"
                params.append(decay.DORMANT_THRESHOLD)
            params.append(limit)
            rows = self._conn.execute(
                f"""
                SELECT f.fact_id, f.content, f.category, f.tags, f.trust_score,
                       f.retrieval_count, f.helpful_count, f.created_at, f.updated_at,
                       f.source, f.notion_page_id
                FROM facts f
                JOIN fact_entities fe ON fe.fact_id = f.fact_id
                JOIN entities e ON e.entity_id = fe.entity_id
                WHERE e.name LIKE ? {cleared_clause}
                ORDER BY f.trust_score DESC, f.retrieval_count DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            # Fallback to FTS
            return self.search(entity, limit=limit, include_cleared=include_cleared)

    def entities_for_fact(self, fact_id: int) -> list[str]:
        """Return all entity names linked to a fact."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.name FROM entities e JOIN fact_entities fe ON fe.entity_id = e.entity_id "
                "WHERE fe.fact_id = ?",
                (fact_id,),
            ).fetchall()
            return [r["name"] for r in rows]

    def reason(
        self, entities: list[str], category: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Compositional query — facts mentioning ALL entities (HRR algebra)."""
        if not self._hrr_available or not entities:
            return self.search(" ".join(entities), category=category, limit=limit)
        with self._lock:
            role_entity = hrr.encode_atom("__hrr_role_entity__", self.hrr_dim)
            role_content = hrr.encode_atom("__hrr_role_content__", self.hrr_dim)
            probe_keys = [
                hrr.bind(hrr.encode_atom(e.lower(), self.hrr_dim), role_entity)
                for e in entities
            ]
            where = "WHERE hrr_vector IS NOT NULL"
            params: list = []
            if category:
                where += " AND category = ?"
                params.append(category)
            rows = self._conn.execute(
                f"SELECT fact_id, content, category, tags, trust_score, retrieval_count, "
                f"helpful_count, created_at, updated_at, source, notion_page_id, hrr_vector "
                f"FROM facts {where}",
                params,
            ).fetchall()
            if not rows:
                return self.search(" ".join(entities), category=category, limit=limit)
            scored = []
            for row in rows:
                fact = dict(row)
                fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
                ent_scores = [
                    hrr.similarity(hrr.unbind(fact_vec, pk), role_content)
                    for pk in probe_keys
                ]
                fact["score"] = (min(ent_scores) + 1.0) / 2.0 * fact["trust_score"]
                scored.append(fact)
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]

    def close(self):
        self._conn.close()

    # ── self-wiring graph (v5) ──────────────────────────────────────

    def self_wire_graph(self, fact_id: int) -> int:
        """Create/strengthen edges between all entities co-occurring in *fact_id*.

        Every pair of entities gets its edge weight incremented by 1.
        Returns the number of edges upserted.
        """
        with self._lock:
            ents = self._conn.execute(
                "SELECT entity_id FROM fact_entities WHERE fact_id = ?",
                (fact_id,),
            ).fetchall()
            ids = [r["entity_id"] for r in ents]
            count = 0
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
                    self._conn.execute(
                        """INSERT INTO entity_graph (entity_a_id, entity_b_id, weight, updated_at)
                           VALUES (?, ?, 1.0, CURRENT_TIMESTAMP)
                           ON CONFLICT(entity_a_id, entity_b_id) DO UPDATE SET
                               weight = weight + 1,
                               updated_at = CURRENT_TIMESTAMP""",
                        (a, b),
                    )
                    count += 1
            self._conn.commit()
            return count

    def entity_neighbors(self, name: str, limit: int = 10) -> list[dict]:
        """Return top-*limit* entities most strongly connected to *name* via co-occurrence."""
        with self._lock:
            row = self._conn.execute(
                "SELECT entity_id FROM entities WHERE name LIKE ?", (name,)
            ).fetchone()
            if not row:
                return []
            eid = row["entity_id"]
            rows = self._conn.execute(
                """SELECT e.name AS neighbor, eg.weight, eg.updated_at
                    FROM entity_graph eg
                    JOIN entities e ON e.entity_id = CASE
                        WHEN eg.entity_a_id = ? THEN eg.entity_b_id
                        ELSE eg.entity_a_id
                    END
                    WHERE eg.entity_a_id = ? OR eg.entity_b_id = ?
                    ORDER BY eg.weight DESC
                    LIMIT ?""",
                (eid, eid, eid, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── contradiction / consistency (v5) ─────────────────────────────

    CONTRADICTION_FLAG = "contradiction_pending"

    def detect_contradictions(
        self, fact_id: int, similarity_threshold: float = CONTRADICTION_THRESHOLD
    ) -> list[dict]:
        """Find existing facts sharing entities with *fact_id* but low content similarity.

        Flags both sides with *contradiction_pending* tag.
        Returns list of dicts: ``{contradictor_id, content, similarity}``.
        """
        with self._lock:
            entities = self.entities_for_fact(fact_id)
            if not entities:
                return []

            new_row = self._conn.execute(
                "SELECT content, tags FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not new_row:
                return []
            new_content = new_row["content"]
            new_tokens = self._tokenize(new_content)

            # Find other facts sharing at least one entity
            placeholders = ",".join("?" for _ in entities)
            others = self._conn.execute(
                f"""SELECT DISTINCT f.fact_id, f.content, f.tags
                    FROM facts f
                    JOIN fact_entities fe ON fe.fact_id = f.fact_id
                    JOIN entities e ON e.entity_id = fe.entity_id
                    WHERE e.name IN ({placeholders})
                      AND f.fact_id != ?""",
                (*entities, fact_id),
            ).fetchall()

            hits: list[dict] = []
            for row in others:
                if _tag_has(row["tags"], self.CONTRADICTION_FLAG):
                    continue  # already flagged
                other_tokens = self._tokenize(row["content"])
                sim = self._jaccard(new_tokens, other_tokens)
                if sim < similarity_threshold:
                    hits.append(
                        {
                            "contradictor_id": int(row["fact_id"]),
                            "content": row["content"],
                            "similarity": round(sim, 4),
                        }
                    )

            if not hits:
                return []

            # Flag both sides
            new_tags = _tag_add(new_row["tags"], self.CONTRADICTION_FLAG)
            self._conn.execute(
                "UPDATE facts SET tags = ? WHERE fact_id = ?",
                (new_tags, fact_id),
            )
            for h in hits:
                old_tags = self._conn.execute(
                    "SELECT tags FROM facts WHERE fact_id = ?",
                    (h["contradictor_id"],),
                ).fetchone()["tags"]
                updated = _tag_add(old_tags, self.CONTRADICTION_FLAG)
                self._conn.execute(
                    "UPDATE facts SET tags = ? WHERE fact_id = ?",
                    (updated, h["contradictor_id"]),
                )
            self._conn.commit()
            return hits

    def resolve_contradictions(self, fact_id: int) -> int:
        """Remove *contradiction_pending* tag from *fact_id* and all facts it contradicts.

        Returns the number of facts un-flagged (including *fact_id*).
        """
        with self._lock:
            count = 0
            # Unflag this fact
            row = self._conn.execute(
                "SELECT tags FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if row and _tag_has(row["tags"], self.CONTRADICTION_FLAG):
                new_tags = _tag_remove(row["tags"], self.CONTRADICTION_FLAG)
                self._conn.execute(
                    "UPDATE facts SET tags = ? WHERE fact_id = ?",
                    (new_tags, fact_id),
                )
                count += 1

            # Find and unflag contradictors (facts sharing entities)
            entities = self.entities_for_fact(fact_id)
            if entities:
                placeholders = ",".join("?" for _ in entities)
                others = self._conn.execute(
                    f"""SELECT f.fact_id, f.tags
                        FROM facts f
                        JOIN fact_entities fe ON fe.fact_id = f.fact_id
                        JOIN entities e ON e.entity_id = fe.entity_id
                        WHERE e.name IN ({placeholders})
                          AND f.fact_id != ?""",
                    (*entities, fact_id),
                ).fetchall()
                for row in others:
                    if _tag_has(row["tags"], self.CONTRADICTION_FLAG):
                        new_tags = _tag_remove(row["tags"], self.CONTRADICTION_FLAG)
                        self._conn.execute(
                            "UPDATE facts SET tags = ? WHERE fact_id = ?",
                            (new_tags, int(row["fact_id"])),
                        )
                        count += 1

            self._conn.commit()
            return count

    def detect_contradictions_for_unflagged(self, limit: int = 20) -> list[dict]:
        """Periodic sweep: check recently-added unflagged facts for contradictions.

        Iterates the *limit* most-recent facts with entities but without the
        *contradiction_pending* flag.  Returns combined list of hits.
        """
        with self._lock:
            candidates = self._conn.execute(
                """SELECT f.fact_id
                    FROM facts f
                    WHERE f.tags NOT LIKE ?
                      AND f.fact_id IN (
                          SELECT DISTINCT fe.fact_id FROM fact_entities fe
                      )
                    ORDER BY f.fact_id DESC
                    LIMIT ?""",
                (f"%{self.CONTRADICTION_FLAG}%", limit),
            ).fetchall()
            all_hits: list[dict] = []
            for (fid,) in candidates:
                hits = self.detect_contradictions(int(fid))
                all_hits.extend(hits)
            return all_hits

    # ── Zettelkasten memory linking (A-MEM) ──────────────────────────
    # A-MEM: Agentic Memory for LLM Agents (Xu et al., 2025).
    # https://github.com/agiresearch/A-mem

    LINK_THRESHOLD = 0.25
    """Minimum Jaccard similarity to create a fact link."""

    EVOLVE_MERGE_THRESHOLD = 0.65
    """Jaccard similarity above which two facts are merged during evolution."""

    def create_links(self, fact_id: int, limit: int = 20) -> list[dict]:
        """Scan existing facts for semantic similarity and create bidirectional links.

        Uses BM25 candidate retrieval + Jaccard reranking to find related
        facts, then upserts bidirectional links weighted by similarity.

        This implements Zettelkasten-style automatic linking:
        every new memory is connected to existing related memories.

        Returns list of created links: ``[{fact_id, content, weight}]``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT content, tags FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not row:
                return []
            content = row["content"]
            tokens = self._tokenize(content)

            # Get BM25 candidates (exclude self)
            candidates = self._fts_candidates(
                content, category=None, source=None, min_trust=0.0, limit=limit
            )
            links: list[dict] = []

            for c in candidates:
                cid = c["fact_id"]
                if cid == fact_id:
                    continue
                other_tokens = self._tokenize(c["content"])
                sim = self._jaccard(tokens, other_tokens)
                if sim >= self.LINK_THRESHOLD:
                    a, b = (fact_id, cid) if fact_id < cid else (cid, fact_id)
                    self._conn.execute(
                        """INSERT INTO fact_links (fact_id_a, fact_id_b, weight, updated_at)
                           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(fact_id_a, fact_id_b) DO UPDATE SET
                               weight = MAX(weight, ?),
                               updated_at = CURRENT_TIMESTAMP""",
                        (a, b, round(sim, 4), round(sim, 4)),
                    )
                    links.append(
                        {
                            "fact_id": cid,
                            "content": c["content"][:120],
                            "weight": round(sim, 4),
                        }
                    )

            self._conn.commit()
            return links

    def linked_facts(self, fact_id: int, limit: int = 10) -> list[dict]:
        """Return facts linked to *fact_id*, ordered by link weight.

        Returns list of ``{fact_id, content, category, trust_score, weight}``.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT f.fact_id, f.content, f.category, f.trust_score, fl.weight
                    FROM fact_links fl
                    JOIN facts f ON f.fact_id = CASE
                        WHEN fl.fact_id_a = ? THEN fl.fact_id_b
                        ELSE fl.fact_id_a
                    END
                    WHERE fl.fact_id_a = ? OR fl.fact_id_b = ?
                    ORDER BY fl.weight DESC, fl.updated_at DESC
                    LIMIT ?""",
                (fact_id, fact_id, fact_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def link_stats(self) -> dict:
        """Return statistics about the fact link graph."""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) as n FROM fact_links"
            ).fetchone()["n"]
            if total == 0:
                return {"total_links": 0, "linked_facts": 0, "avg_links_per_fact": 0.0}
            linked = self._conn.execute(
                "SELECT COUNT(DISTINCT fact_id_a) + COUNT(DISTINCT fact_id_b) as n FROM fact_links"
            ).fetchone()["n"]
            return {
                "total_links": int(total),
                "linked_facts": int(linked),
                "avg_links_per_fact": round(total / max(linked, 1), 2),
            }

    def evolve(self, threshold: float | None = None) -> dict:
        """Memory evolution pass: merge near-duplicate facts.

        Scans all facts for pairs with Jaccard similarity >= threshold.
        When a pair is found, the fact with lower ID is kept and the other
        is merged into it (content combined, trust averaged, entities merged).
        The lower-ID fact's links are preserved.

        Returns dict with counts of merges performed.
        """
        t = self.EVOLVE_MERGE_THRESHOLD if threshold is None else float(threshold)
        with self._lock:
            all_facts = self._conn.execute(
                "SELECT fact_id, content, trust_score, tags, category, source FROM facts "
                "ORDER BY fact_id"
            ).fetchall()
            if len(all_facts) < 2:
                return {"merged": 0, "candidates_checked": 0}

            merged = 0
            checked = 0
            skip_ids: set[int] = set()

            for i in range(len(all_facts)):
                fa = all_facts[i]
                if int(fa["fact_id"]) in skip_ids:
                    continue
                fa_tokens = self._tokenize(fa["content"])

                for j in range(i + 1, len(all_facts)):
                    fb = all_facts[j]
                    if int(fb["fact_id"]) in skip_ids:
                        continue
                    fb_tokens = self._tokenize(fb["content"])
                    sim = self._jaccard(fa_tokens, fb_tokens)
                    checked += 1

                    if float(sim) >= float(t):
                        # Merge fb into fa (keep older/lower ID)
                        keep_id = int(fa["fact_id"])
                        merge_id = int(fb["fact_id"])

                        # Combine content (append, deduplicate)
                        new_content = fa["content"]
                        fb_content = fb["content"]
                        if (
                            fb_content not in new_content
                            and fa["content"] not in fb_content
                        ):
                            new_content = fa["content"] + "\n\n---\n\n" + fb_content
                        elif len(fb_content) > len(fa["content"]):
                            new_content = fb_content  # keep the longer one

                        # Average trust
                        new_trust = (fa["trust_score"] + fb["trust_score"]) / 2.0

                        # Merge tags
                        all_tags = set()
                        for t in fa["tags"].split(","):
                            if t.strip():
                                all_tags.add(t.strip())
                        for t in fb["tags"].split(","):
                            if t.strip():
                                all_tags.add(t.strip())
                        new_tags = ",".join(sorted(all_tags))

                        # Transfer entity links from merge_id to keep_id
                        existing_ents = set(
                            r["entity_id"]
                            for r in self._conn.execute(
                                "SELECT entity_id FROM fact_entities WHERE fact_id = ?",
                                (keep_id,),
                            ).fetchall()
                        )
                        merge_ents = self._conn.execute(
                            "SELECT entity_id FROM fact_entities WHERE fact_id = ?",
                            (merge_id,),
                        ).fetchall()
                        for (eid,) in merge_ents:
                            if eid not in existing_ents:
                                self._conn.execute(
                                    "INSERT OR IGNORE INTO fact_entities VALUES (?, ?)",
                                    (keep_id, eid),
                                )

                        # Transfer fact_links from merge_id to keep_id
                        merge_links = self._conn.execute(
                            """SELECT fact_id_a, fact_id_b, weight
                                FROM fact_links
                                WHERE fact_id_a = ? OR fact_id_b = ?""",
                            (merge_id, merge_id),
                        ).fetchall()
                        for link_row in merge_links:
                            other_id = (
                                link_row["fact_id_a"]
                                if link_row["fact_id_b"] == merge_id
                                else link_row["fact_id_b"]
                            )
                            if other_id == keep_id:
                                continue  # already linked
                            a, b = (
                                (keep_id, other_id)
                                if keep_id < other_id
                                else (other_id, keep_id)
                            )
                            self._conn.execute(
                                """INSERT INTO fact_links (fact_id_a, fact_id_b, weight, updated_at)
                                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                                   ON CONFLICT(fact_id_a, fact_id_b) DO UPDATE SET
                                       weight = MAX(weight, ?),
                                       updated_at = CURRENT_TIMESTAMP""",
                                (a, b, link_row["weight"], link_row["weight"]),
                            )

                        # Update keep fact
                        self._conn.execute(
                            "UPDATE facts SET content = ?, trust_score = ?, tags = ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                            (new_content, new_trust, new_tags, keep_id),
                        )

                        # Recompute HRR vector for the merged fact
                        self._compute_hrr_vector(keep_id, new_content)

                        # Delete the merged fact (cascades to fact_entities, fact_links)
                        self._conn.execute(
                            "DELETE FROM facts WHERE fact_id = ?", (merge_id,)
                        )

                        skip_ids.add(merge_id)
                        merged += 1

            self._conn.commit()
            return {"merged": merged, "candidates_checked": checked}

    # ── Temporal Queries (Memvid-inspired) ────────────────────────────

    @staticmethod
    def parse_time_query(query: str) -> tuple[str | None, str | None]:
        """Parse a natural language time query into (start_date, end_date) ISO strings.

        Handles English + Indonesian temporal expressions.
        Returns (None, None) when no temporal info is detected.
        """
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        start: datetime | None = None
        end: datetime | None = None

        for pattern, unit, direction in _RELATIVE_PATTERNS:
            m = pattern.search(query)
            if not m:
                continue

            if unit == "num":
                num = int(m.group(1))
                unit_str = m.group(2).lower()
                if unit_str in ("minutes", "menit"):
                    delta = timedelta(minutes=num)
                elif unit_str in ("hours", "h", "jam"):
                    delta = timedelta(hours=num)
                else:
                    delta = timedelta(days=num)
                if direction < 0:  # last/past
                    start = now - delta
                    end = now
                else:  # next/coming
                    start = now
                    end = now + delta
                break
            elif unit == "day":
                start = now + timedelta(days=direction)
                end = start + timedelta(days=1)
            elif unit == "week":
                # Monday of the week
                week_start = now - timedelta(days=now.weekday())
                start = week_start + timedelta(weeks=direction)
                end = start + timedelta(weeks=1)
            elif unit == "month":
                month_start = now.replace(day=1)
                # Approximate months
                month = month_start.month + direction
                year = month_start.year
                while month < 1:
                    month += 12
                    year -= 1
                while month > 12:
                    month -= 12
                    year += 1
                start = month_start.replace(year=year, month=month)
                # End = first day of next month
                if month == 12:
                    end = start.replace(year=year + 1, month=1)
                else:
                    end = start.replace(month=month + 1)
            elif unit == "year":
                year_val = now.year + direction
                start = now.replace(year=year_val, month=1, day=1)
                end = now.replace(year=year_val + 1, month=1, day=1)
            break

        # Check for absolute date patterns
        if start is None and end is None:
            for pattern, kind in _ABSOLUTE_DATE_PATTERNS:
                m = pattern.search(query)
                if m:
                    if kind == "iso":
                        start = datetime(
                            int(m.group(1)), int(m.group(2)), int(m.group(3))
                        )
                        end = start + timedelta(days=1)
                    elif kind == "us":
                        start = datetime(
                            int(m.group(3)), int(m.group(1)), int(m.group(2))
                        )
                        end = start + timedelta(days=1)
                    elif kind == "named":
                        months = {
                            "jan": 1,
                            "feb": 2,
                            "mar": 3,
                            "apr": 4,
                            "may": 5,
                            "jun": 6,
                            "jul": 7,
                            "aug": 8,
                            "sep": 9,
                            "oct": 10,
                            "nov": 11,
                            "dec": 12,
                        }
                        start = datetime(
                            int(m.group(3)),
                            months[m.group(2).lower()[:3]],
                            int(m.group(1)),
                        )
                        end = start + timedelta(days=1)
                    break

        return (
            start.isoformat() if start else None,
            end.isoformat() if end else None,
        )

    @staticmethod
    def has_temporal_intent(query: str) -> bool:
        """Check if a search query has temporal filtering intent."""
        tokens = set(query.lower().split())
        if tokens & _TEMPORAL_INTENT_KEYWORDS:
            return True
        # Check date patterns
        for pattern, _kind in _ABSOLUTE_DATE_PATTERNS:
            if pattern.search(query):
                return True
        return False

    def search_temporal(
        self,
        query: str,
        time_start: str | None = None,
        time_end: str | None = None,
        category: str | None = None,
        source: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
        include_cleared: bool = False,
    ) -> list[dict]:
        """Search with optional time-window filter.

        When time_start is set, filters facts where created_at falls within
        [time_start, time_end). When time_end is omitted, defaults to now.
        """
        if not time_start and not time_end:
            return self.search(
                query,
                category=category,
                source=source,
                min_trust=min_trust,
                limit=limit,
                include_cleared=include_cleared,
            )

        with self._lock:
            query = query.strip()
            if not query:
                return []

            candidates = self._fts_candidates(
                query, category, source, min_trust, limit * 3
            )
            if not candidates:
                return []

            if not include_cleared:
                candidates = [
                    c
                    for c in candidates
                    if c.get("strength", 1.0) > decay.DORMANT_THRESHOLD
                ]
                if not candidates:
                    return []

            # Apply time filter
            filtered = []
            for c in candidates:
                created = c.get("created_at", "")
                if not created:
                    continue
                if time_start and str(created) < str(time_start):
                    continue
                if time_end and str(created) >= str(time_end):
                    continue
                filtered.append(c)
            candidates = filtered

            if not candidates:
                return []

            query_tokens = self._tokenize(query)
            candidate_ids = [c["fact_id"] for c in candidates]

            emb_scores: dict[int, float] = {}
            if self.embedding_index and self.embedding_index.available:
                emb_scores = self.embedding_index.search(query, candidate_ids)

            scored = []
            for fact in candidates:
                content_tokens = self._tokenize(fact["content"])
                tag_tokens = self._tokenize(fact.get("tags") or "")
                jaccard = self._jaccard(query_tokens, content_tokens | tag_tokens)
                fts_score = fact.get("fts_rank", 0.0)
                if self.hrr_weight > 0 and fact.get("hrr_vector"):
                    fact_vec = hrr.bytes_to_phases(fact["hrr_vector"])
                    query_vec = hrr.encode_text(query, self.hrr_dim)
                    hrr_sim = (hrr.similarity(query_vec, fact_vec) + 1.0) / 2.0
                else:
                    hrr_sim = 0.5
                emb_sim = emb_scores.get(fact["fact_id"], 0.5)
                relevance = (
                    self.fts_weight * fts_score
                    + self.jaccard_weight * jaccard
                    + self.hrr_weight * hrr_sim
                    + 0.1 * emb_sim
                )
                fact["score"] = relevance * fact["trust_score"]
                fact.pop("hrr_vector", None)
                scored.append(fact)

            scored.sort(key=lambda x: x["score"], reverse=True)
            results = scored[:limit]
            for fact in results:
                self._boost_strength(fact["fact_id"], decay.READ_BOOST)
            self._conn.commit()
            return results

    # ── Per-Fact Versioning (Memvid-inspired) ─────────────────────────

    def versioned_update(
        self,
        fact_id: int,
        new_content: str,
        reason: str = "",
    ) -> dict | None:
        """Update a fact with full version tracking (append-only).

        Creates a new version snapshot before applying the update.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT content, category, tags, trust_score, source FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if not row:
                return None

            old_content = row["content"]
            if old_content == new_content.strip():
                return {
                    "fact_id": fact_id,
                    "changed": False,
                    "message": "content unchanged",
                }

            # Snapshot current state into versions table
            self._conn.execute(
                "INSERT INTO fact_versions (fact_id, content, category, tags, trust_score, source, action, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, 'updated', ?)",
                (
                    fact_id,
                    new_content.strip(),
                    row["category"],
                    row["tags"],
                    row["trust_score"],
                    row["source"],
                    reason,
                ),
            )

            # Apply the update (may fail if new_content conflicts with another fact's UNIQUE content)
            try:
                self._conn.execute(
                    "UPDATE facts SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                    (new_content.strip(), fact_id),
                )
            except sqlite3.IntegrityError:
                logger.warning(
                    "versioned_update: content '%s' conflicts with existing fact UNIQUE constraint",
                    new_content.strip()[:60],
                )
                return {
                    "fact_id": fact_id,
                    "changed": False,
                    "error": "content conflicts with existing fact UNIQUE constraint",
                    "detail": "Another fact already has this exact content. Use a different wording or reference the existing fact.",
                }

            # Recompute HRR
            self._compute_hrr_vector(fact_id, new_content.strip())

            # Update embedding
            if self.embedding_index:
                self.embedding_index.index_fact(fact_id, new_content.strip())

            self._conn.commit()

            return {
                "fact_id": fact_id,
                "changed": True,
                "old_content": old_content[:120],
                "new_content": new_content.strip()[:120],
                "reason": reason,
            }

    def get_version_history(self, fact_id: int, limit: int = 20) -> list[dict]:
        """Return all version records for a fact, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT version_id, fact_id, content, action, reason, created_at "
                "FROM fact_versions WHERE fact_id = ? "
                "ORDER BY version_id DESC LIMIT ?",
                (fact_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def undo_to_version(self, fact_id: int, version_id: int) -> dict | None:
        """Rollback a fact to a previous version.

        The current state is saved as a new version (action='undo_checkpoint')
        before the rollback, so undo itself is reversible.
        """
        with self._lock:
            # Get current state
            current = self._conn.execute(
                "SELECT content, category, tags, trust_score, source FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if not current:
                return None

            # Get target version
            target = self._conn.execute(
                "SELECT content, category, tags, trust_score, source FROM fact_versions "
                "WHERE version_id = ? AND fact_id = ?",
                (version_id, fact_id),
            ).fetchone()
            if not target:
                return None

            # Save current state as checkpoint before rolling back
            self._conn.execute(
                "INSERT INTO fact_versions (fact_id, content, category, tags, trust_score, source, action, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, 'undo_checkpoint', ?)",
                (
                    fact_id,
                    current["content"],
                    current["category"],
                    current["tags"],
                    current["trust_score"],
                    current["source"],
                    f"undo_to_version_{version_id}",
                ),
            )

            # Restore target version content to main table
            self._conn.execute(
                "UPDATE facts SET content = ?, category = ?, tags = ?, trust_score = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                (
                    target["content"],
                    target["category"],
                    target["tags"],
                    target["trust_score"],
                    fact_id,
                ),
            )

            # Recompute HRR vector
            self._compute_hrr_vector(fact_id, target["content"])

            # Re-index embedding
            if self.embedding_index:
                self.embedding_index.index_fact(fact_id, target["content"])

            self._conn.commit()

            return {
                "fact_id": fact_id,
                "restored_content": target["content"][:120],
                "from_version": version_id,
                "checkpoint_saved": True,
            }

    def versioning_stats(self) -> dict:
        """Return versioning statistics."""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) as n FROM fact_versions"
            ).fetchone()["n"]
            versioned_facts = self._conn.execute(
                "SELECT COUNT(DISTINCT fact_id) as n FROM fact_versions"
            ).fetchone()["n"]
            actions = self._conn.execute(
                "SELECT action, COUNT(*) as n FROM fact_versions GROUP BY action"
            ).fetchall()
            max_per_fact = self._conn.execute(
                "SELECT fact_id, COUNT(*) as n FROM fact_versions GROUP BY fact_id ORDER BY n DESC LIMIT 1"
            ).fetchone()
            return {
                "total_versions": int(total),
                "versioned_facts": int(versioned_facts),
                "actions": {r["action"]: int(r["n"]) for r in actions},
                "max_versions_per_fact": int(max_per_fact["n"]) if max_per_fact else 0,
            }

    def flush(self):
        """Flush pending writes to disk (WAL checkpoint)."""
        try:
            self._conn.commit()
        except Exception:
            logger.debug("facts flush commit failed (non-fatal)")

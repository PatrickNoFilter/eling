"""Facts layer — SQLite-backed fact store with HRR + BM25 + Jaccard hybrid retrieval.

Adapted from the holographic memory plugin by dusterbloom (Hermes PR #2351, MIT).
Standalone — accepts any db_path, no Hermes dependencies.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import hrr
from .. import decay

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

_RE_CAPITALIZED = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
_RE_DOUBLE_QUOTE = re.compile(r'"([^"]+)"')
_RE_SINGLE_QUOTE = re.compile(r"'([^']+)'")


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
    ):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_trust = _clamp(default_trust)
        self.hrr_dim = hrr_dim
        self._hrr_available = hrr._HAS_NUMPY

        if hrr_weight > 0 and not self._hrr_available:
            fts_weight, jaccard_weight, hrr_weight = 0.6, 0.4, 0.0
        self.fts_weight = fts_weight
        self.jaccard_weight = jaccard_weight
        self.hrr_weight = hrr_weight

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._conn.executescript(_SCHEMA)
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
    def add(self, content: str, category: str = "general", tags: str = "", source: str = "facts") -> int:
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
            except sqlite3.IntegrityError:
                row = self._conn.execute("SELECT fact_id FROM facts WHERE content = ?", (content,)).fetchone()
                return int(row["fact_id"])

            for name in self._extract_entities(content):
                eid = self._resolve_entity(name)
                self._conn.execute("INSERT OR IGNORE INTO fact_entities VALUES (?, ?)", (fact_id, eid))
            self._compute_hrr_vector(fact_id, content)
            self._conn.commit()
            return int(fact_id)

    def remove(self, fact_id: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT fact_id FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,))
            self._conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            self._conn.commit()
            return True

    def update_trust(self, fact_id: int, helpful: bool) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT trust_score, helpful_count, strength FROM facts WHERE fact_id = ?", (fact_id,)
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
            return {"fact_id": fact_id, "trust_score": new_trust, "strength": new_strength}

    def set_notion_page(self, fact_id: int, notion_page_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE facts SET notion_page_id = ? WHERE fact_id = ?", (notion_page_id, fact_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ---- search ops ----
    def search(self, query: str, category: str | None = None, source: str | None = None, min_trust: float = 0.3, limit: int = 10, include_cleared: bool = False) -> list[dict]:
        """Hybrid BM25 + Jaccard + HRR search. Filter by category or source.

        Excludes cleared facts (strength <= DORMANT_THRESHOLD) unless include_cleared=True.
        Boosts strength + updates last_access_at for returned facts.
        """
        with self._lock:
            query = query.strip()
            if not query:
                return []
            candidates = self._fts_candidates(query, category, source, min_trust, limit * 3)
            if not candidates:
                return []
            # Filter out cleared facts unless explicitly included
            if not include_cleared:
                candidates = [c for c in candidates if c.get("strength", 1.0) > decay.DORMANT_THRESHOLD]
                if not candidates:
                    return []
            query_tokens = self._tokenize(query)
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
                relevance = self.fts_weight * fts_score + self.jaccard_weight * jaccard + self.hrr_weight * hrr_sim
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

    def _fts_candidates(self, query: str, category: str | None, source: str | None, min_trust: float, limit: int) -> list[dict]:
        params = [query, min_trust]
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
        except sqlite3.OperationalError:
            return []
        cands = [dict(r) for r in rows]
        if cands:
            max_rank = max(c.get("fts_rank", 0.0) for c in cands) or 1.0
            for c in cands:
                c["fts_rank"] = c.get("fts_rank", 0.0) / max_rank
        return cands

    def list_all(self, category: str | None = None, min_trust: float = 0.0, limit: int = 100, include_cleared: bool = False) -> list[dict]:
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
                "FROM facts WHERE fact_id = ?", (fact_id,)
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
                "WHERE fact_id = ?", (score, fact_id)
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
            total = self._conn.execute("SELECT COUNT(*) as n FROM facts").fetchone()["n"]
            active = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ?", (decay.ACTIVE_THRESHOLD,)
            ).fetchone()["n"]
            dormant = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ? AND strength <= ?",
                (decay.DORMANT_THRESHOLD, decay.ACTIVE_THRESHOLD),
            ).fetchone()["n"]
            cleared = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength <= ?", (decay.DORMANT_THRESHOLD,)
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
                "SELECT COUNT(*) as n FROM facts WHERE strength > ?", (decay.ACTIVE_THRESHOLD,)
            ).fetchone()["n"]
            dormant = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength > ? AND strength <= ?",
                (decay.DORMANT_THRESHOLD, decay.ACTIVE_THRESHOLD),
            ).fetchone()["n"]
            cleared = self._conn.execute(
                "SELECT COUNT(*) as n FROM facts WHERE strength <= ?", (decay.DORMANT_THRESHOLD,)
            ).fetchone()["n"]
            return {
                "total_facts": counts["n"],
                "total_entities": ents["n"],
                "by_category": {r["category"]: r["n"] for r in cats},
                "hrr_enabled": self._hrr_available,
                "active_facts": int(active),
                "dormant_facts": int(dormant),
                "cleared_facts": int(cleared),
            }

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
        for m in _RE_CAPITALIZED.finditer(text):
            _add(m.group(1))
        for m in _RE_DOUBLE_QUOTE.finditer(text):
            _add(m.group(1))
        for m in _RE_SINGLE_QUOTE.finditer(text):
            _add(m.group(1))
        return out

    def _resolve_entity(self, name: str) -> int:
        row = self._conn.execute("SELECT entity_id FROM entities WHERE name LIKE ?", (name,)).fetchone()
        if row:
            return int(row["entity_id"])
        cur = self._conn.execute("INSERT INTO entities (name) VALUES (?)", (name,))
        return int(cur.lastrowid)

    def _compute_hrr_vector(self, fact_id: int, content: str) -> None:
        if not self._hrr_available:
            return
        rows = self._conn.execute(
            "SELECT e.name FROM entities e JOIN fact_entities fe ON fe.entity_id = e.entity_id "
            "WHERE fe.fact_id = ?", (fact_id,)
        ).fetchall()
        entities = [r["name"] for r in rows]
        vector = hrr.encode_fact(content, entities, self.hrr_dim)
        self._conn.execute(
            "UPDATE facts SET hrr_vector = ? WHERE fact_id = ?",
            (hrr.phases_to_bytes(vector), fact_id),
        )

    def probe(self, entity: str, limit: int = 10, include_cleared: bool = False) -> list[dict]:
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
                "WHERE fe.fact_id = ?", (fact_id,)
            ).fetchall()
            return [r["name"] for r in rows]

    def reason(self, entities: list[str], category: str | None = None, limit: int = 10) -> list[dict]:
        """Compositional query — facts mentioning ALL entities (HRR algebra)."""
        if not self._hrr_available or not entities:
            return self.search(" ".join(entities), category=category, limit=limit)
        with self._lock:
            role_entity = hrr.encode_atom("__hrr_role_entity__", self.hrr_dim)
            role_content = hrr.encode_atom("__hrr_role_content__", self.hrr_dim)
            probe_keys = [hrr.bind(hrr.encode_atom(e.lower(), self.hrr_dim), role_entity) for e in entities]
            where = "WHERE hrr_vector IS NOT NULL"
            params: list = []
            if category:
                where += " AND category = ?"
                params.append(category)
            rows = self._conn.execute(
                f"SELECT fact_id, content, category, tags, trust_score, retrieval_count, "
                f"helpful_count, created_at, updated_at, source, notion_page_id, hrr_vector "
                f"FROM facts {where}", params,
            ).fetchall()
            if not rows:
                return self.search(" ".join(entities), category=category, limit=limit)
            scored = []
            for row in rows:
                fact = dict(row)
                fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
                ent_scores = [hrr.similarity(hrr.unbind(fact_vec, pk), role_content) for pk in probe_keys]
                fact["score"] = (min(ent_scores) + 1.0) / 2.0 * fact["trust_score"]
                scored.append(fact)
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]

    def close(self):
        self._conn.close()

    def flush(self):
        """Flush pending writes to disk (WAL checkpoint)."""
        try:
            self._conn.commit()
        except Exception:
            pass

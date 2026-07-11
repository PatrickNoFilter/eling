"""Continuum Layer 6 — orchestration state store.

A single SQLite database backing the Continuum orchestration tier:
  - projects    : canonical project roots (one per repo)
  - knowledge   : two-tier lessons (fundamental = binding, situational = searchable)
                  indexed with FTS5 (BM25) + optional semantic vectors via
                  eling's embeddings layer (gracefully degrades when absent)
  - agents      : multi-agent dispatch registry with a state machine and
                  reserved-path collision tracking

Designed for Termux / PRoot: pure stdlib + sqlite3 (FTS5 ships with Python's
sqlite3 on modern builds). No numpy, no Node, no native build required.

SQLite pragmas mirror Continuum's resilience choices (WAL, FK, busy_timeout).
"""

from __future__ import annotations

import json
import logging
import pickle
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Agent lifecycle legal transitions (mirrors Continuum).
# draft -> active -> merged | abandoned
AGENT_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active"},
    "active": {"merged", "abandoned"},
    "merged": set(),
    "abandoned": set(),
}

# Knowledge tiers — mirrors Continuum's two-tier model.
KNOWLEDGE_KINDS = ("fundamental", "situational")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    rowid   INTEGER PRIMARY KEY,
    path    TEXT NOT NULL UNIQUE,          -- canonical absolute project root
    name    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge (
    slug      TEXT NOT NULL,
    project   TEXT NOT NULL,
    agent_slug TEXT NOT NULL DEFAULT 'continuum',
    kind      TEXT NOT NULL DEFAULT 'situational',  -- fundamental | situational
    title     TEXT NOT NULL DEFAULT '',
    content   TEXT NOT NULL,
    vec       BLOB,                          -- optional serialized embedding
    embed_model TEXT NOT NULL DEFAULT '',    -- freshness profile: model that produced vec
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project, slug)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge(project, kind);

CREATE TABLE IF NOT EXISTS agents (
    slug          TEXT NOT NULL,
    project       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft|active|merged|abandoned
    branch        TEXT NOT NULL DEFAULT '',
    worktree      TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',        -- ready-to-paste dispatch prompt
    merged_commit TEXT NOT NULL DEFAULT '',        -- 7-40 char SHA when merged
    reserved_paths TEXT NOT NULL DEFAULT '[]',     -- JSON array of globs
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (slug, project)
);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(project, status);

-- Plot protocol per project (minimal unified-diff mutable doc).
CREATE TABLE IF NOT EXISTS plot (
    project TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# FTS5 virtual table for BM25 across knowledge body. Mirrors Continuum's
# "metadata-first, cheap top-K" philosophy: search returns metadata, then
# knowledge_get fetches the body.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    slug, project, agent_slug, kind, title, content,
    content='knowledge', content_rowid='rowid', tokenize='porter unicode61'
);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=5.0)
    con.row_factory = sqlite3.Row
    # Resilience: mirror Continuum's busy_timeout + WAL + FK.
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


class ContinuumStore:
    """Persistence + orchestration logic for the Continuum Layer 6 tier."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            # Default lives beside eling's other DBs (ELING_HOME aware).
            home = Path(
                __import__("os").environ.get("ELING_HOME")
                or (__import__("os").environ.get("HERMES_HOME", "") + "/eling")
                or Path.home() / ".eling"
            ).expanduser()
            home.mkdir(parents=True, exist_ok=True)
            db_path = home / "continuum.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = _connect(self.db_path)
        self._migrate()
        self._ensure_fts()

    # ── schema ──

    def _migrate(self) -> None:
        with self._lock:
            self._con.executescript(_SCHEMA)
            self._con.commit()

    def _ensure_fts(self) -> None:
        try:
            with self._lock:
                self._con.execute(_FTS_SCHEMA)
                self._con.commit()
        except sqlite3.OperationalError as exc:
            # FTS5 may be unavailable on some minimal Android sqlite builds.
            # Fall back to LIKE-based search (see knowledge_search).
            logger.warning("FTS5 unavailable (%s) — falling back to LIKE search", exc)
            self._fts_available = False
        else:
            self._fts_available = True
            self._rebuild_fts()

    def _rebuild_fts(self) -> None:
        if not getattr(self, "_fts_available", False):
            return
        with self._lock:
            self._con.execute(
                "INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')"
            )
            self._con.commit()

    # ── projects ──

    def project_create(self, path: str, name: str | None = None) -> dict:
        path = str(Path(path).expanduser().resolve())
        if name is None:
            name = Path(path).name
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO projects(path, name) VALUES(?, ?) "
                "ON CONFLICT(path) DO UPDATE SET name=excluded.name, updated_at=datetime('now') "
                "RETURNING path",
                (path, name),
            )
            row = cur.fetchone()
            self._con.commit()
        return {"path": row["path"], "name": name, "created": True}

    def project_get(self, path: str) -> dict | None:
        path = str(Path(path).expanduser().resolve())
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM projects WHERE path=?", (path,)
            ).fetchone()
        return dict(row) if row else None

    def project_list(self) -> list[dict]:
        with self._lock:
            rows = self._con.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    # ── knowledge (two-tier) ──

    def knowledge_create(
        self,
        project: str,
        slug: str,
        content: str,
        kind: str = "situational",
        agent_slug: str = "continuum",
        title: str = "",
        embed: bool | None = None,
    ) -> dict:
        if kind not in KNOWLEDGE_KINDS:
            raise ValueError(f"kind must be one of {KNOWLEDGE_KINDS}, got {kind!r}")
        project = str(Path(project).expanduser().resolve())
        vec, model = None, ""
        if embed is not False:
            vec, model = self._maybe_embed(content)
        with self._lock:
            self._con.execute(
                "INSERT INTO knowledge(slug, project, agent_slug, kind, title, content, vec, embed_model) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project, slug) DO UPDATE SET "
                "content=excluded.content, kind=excluded.kind, title=excluded.title, "
                "vec=excluded.vec, embed_model=excluded.embed_model, updated_at=datetime('now')",
                (slug, project, agent_slug, kind, title, content, vec, model),
            )
            self._con.commit()
        if getattr(self, "_fts_available", False):
            self._rebuild_fts()
        return {"project": project, "slug": slug, "kind": kind, "embedded": bool(vec)}

    def knowledge_get(self, project: str, slug: str) -> dict | None:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM knowledge WHERE project=? AND slug=?", (project, slug)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d.pop("vec", None)  # never ship the raw blob back
        return d

    def knowledge_list(self, project: str, kind: str | None = None) -> list[dict]:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            if kind:
                rows = self._con.execute(
                    "SELECT slug, project, agent_slug, kind, title, created_at, updated_at "
                    "FROM knowledge WHERE project=? AND kind=? ORDER BY updated_at DESC",
                    (project, kind),
                ).fetchall()
            else:
                rows = self._con.execute(
                    "SELECT slug, project, agent_slug, kind, title, created_at, updated_at "
                    "FROM knowledge WHERE project=? ORDER BY updated_at DESC",
                    (project,),
                ).fetchall()
        return [
            {
                k: r[k]
                for k in (
                    "slug",
                    "project",
                    "agent_slug",
                    "kind",
                    "title",
                    "created_at",
                    "updated_at",
                )
            }
            for r in rows
        ]

    def knowledge_search(self, project: str, q: str, limit: int = 10) -> list[dict]:
        """Metadata-only ranked search (cheap top-K). Call knowledge_get for body.

        Uses FTS5 BM25 when available, else LIKE fallback.
        """
        project = str(Path(project).expanduser().resolve())
        qp = q.strip()
        if not qp:
            return []
        with self._lock:
            if getattr(self, "_fts_available", False):
                fts_q = self._fts_query(qp)
                rows = self._con.execute(
                    "SELECT k.slug, k.project, k.agent_slug, k.kind, k.title, k.updated_at, "
                    "rank FROM knowledge_fts f JOIN knowledge k ON k.rowid=f.rowid "
                    "WHERE knowledge_fts MATCH ? AND k.project=? ORDER BY rank LIMIT ?",
                    (fts_q, project, limit),
                ).fetchall()
            else:
                like = f"%{qp}%"
                rows = self._con.execute(
                    "SELECT slug, project, agent_slug, kind, title, updated_at FROM knowledge "
                    "WHERE project=? AND (content LIKE ? OR title LIKE ?) "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (project, like, like, limit),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("rank", None)
            out.append(d)
        return out

    @staticmethod
    def _fts_query(q: str) -> str:
        # Quote bare words, OR them so natural language works (fuzzy, not exact).
        toks = [t for t in q.replace('"', " ").split() if t]
        if not toks:
            return q
        return " OR ".join(f'"{t}"' for t in toks)

    # ── agents (registry + state machine + reserved paths) ──

    def agent_register(
        self,
        project: str,
        slug: str,
        branch: str = "",
        worktree: str = "",
        prompt: str = "",
        reserved_paths: list[str] | None = None,
        agent_slug: str = "continuum",
    ) -> dict:
        project = str(Path(project).expanduser().resolve())
        rp = reserved_paths or []
        with self._lock:
            self._con.execute(
                "INSERT INTO agents(slug, project, status, branch, worktree, prompt, reserved_paths) "
                "VALUES(?, ?, 'draft', ?, ?, ?, ?) "
                "ON CONFLICT(slug, project) DO UPDATE SET "
                "branch=excluded.branch, worktree=excluded.worktree, prompt=excluded.prompt, "
                "reserved_paths=excluded.reserved_paths, updated_at=datetime('now')",
                (slug, project, branch, worktree, prompt, json.dumps(rp)),
            )
            self._con.commit()
        result = {
            "project": project,
            "slug": slug,
            "status": "draft",
            "agent_slug": agent_slug,
        }
        # Surface collisions immediately so the orchestrator can decide.
        collisions = self._reservation_collisions(project, slug, rp)
        if collisions:
            result["reservation_warning"] = collisions
        return result

    def agent_update(
        self,
        project: str,
        slug: str,
        status: str | None = None,
        branch: str | None = None,
        worktree: str | None = None,
        prompt: str | None = None,
        merged_commit: str | None = None,
        reserved_paths: list[str] | None = None,
        agent_slug: str | None = None,
    ) -> dict:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            cur = self._con.execute(
                "SELECT * FROM agents WHERE project=? AND slug=?", (project, slug)
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"agent not found: {slug} in {project}")
            current = dict(row)

        if status is not None and status != current["status"]:
            allowed = AGENT_TRANSITIONS.get(current["status"], set())
            if status not in allowed:
                raise ValueError(
                    f"invalid_transition: {current['status']} -> {status}. "
                    f"Allowed: {sorted(allowed) or 'none'}"
                )
            if status == "merged":
                mc = (
                    merged_commit
                    if merged_commit is not None
                    else current["merged_commit"]
                )
                if not mc or not (7 <= len(mc) <= 40):
                    raise ValueError(
                        "missing_merged_commit: merged requires a 7-40 char SHA"
                    )

        sets, params = [], []
        for col, val in (
            ("status", status),
            ("branch", branch),
            ("worktree", worktree),
            ("prompt", prompt),
            ("merged_commit", merged_commit),
        ):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        if reserved_paths is not None:
            sets.append("reserved_paths=?")
            params.append(json.dumps(reserved_paths))
        if sets:
            sets.append("updated_at=datetime('now')")
            params.extend([project, slug])
            with self._lock:
                self._con.execute(
                    f"UPDATE agents SET {', '.join(sets)} WHERE project=? AND slug=?",
                    params,
                )
                self._con.commit()
        result = {
            "project": project,
            "slug": slug,
            "status": status or current["status"],
        }
        if reserved_paths is not None:
            collisions = self._reservation_collisions(project, slug, reserved_paths)
            if collisions:
                result["reservation_warning"] = collisions
        return result

    def agent_get(self, project: str, slug: str) -> dict | None:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            row = self._con.execute(
                "SELECT * FROM agents WHERE project=? AND slug=?", (project, slug)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["reserved_paths"] = json.loads(d.get("reserved_paths") or "[]")
        return d

    def registry_list(
        self, project: str, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            if status:
                rows = self._con.execute(
                    "SELECT slug, project, status, branch, worktree, reserved_paths, "
                    "json_array_length(reserved_paths) AS reserved_count, updated_at "
                    "FROM agents WHERE project=? AND status=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (project, status, limit, offset),
                ).fetchall()
            else:
                rows = self._con.execute(
                    "SELECT slug, project, status, branch, worktree, reserved_paths, "
                    "json_array_length(reserved_paths) AS reserved_count, updated_at "
                    "FROM agents WHERE project=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (project, limit, offset),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["reserved_paths"] = json.loads(d.get("reserved_paths") or "[]")
            out.append(d)
        return out

    def _reservation_collisions(
        self, project: str, self_slug: str, reserved: list[str]
    ) -> list[dict]:
        """Find other ACTIVE agents whose reserved globs overlap ours.

        Queries the agents table directly (status='active', slug != self) so it
        does not depend on registry_list's column projection.
        """
        if not reserved:
            return []
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            rows = self._con.execute(
                "SELECT slug, reserved_paths FROM agents WHERE project=? AND status='active' AND slug!=?",
                (project, self_slug),
            ).fetchall()
        hits = []
        for r in rows:
            other_rp = json.loads(r["reserved_paths"] or "[]")
            for pat in reserved:
                if pat in other_rp:
                    hits.append({"agent": r["slug"], "glob": pat})
        return hits

    # ── plot protocol ──

    def plot_get(self, project: str) -> str | None:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            row = self._con.execute(
                "SELECT content FROM plot WHERE project=?", (project,)
            ).fetchone()
        return row["content"] if row else None

    def plot_set(self, project: str, content: str) -> dict:
        project = str(Path(project).expanduser().resolve())
        with self._lock:
            self._con.execute(
                "INSERT INTO plot(project, content) VALUES(?, ?) "
                "ON CONFLICT(project) DO UPDATE SET content=excluded.content, updated_at=datetime('now')",
                (project, content),
            )
            self._con.commit()
        return {"project": project, "updated": True}

    # ── embed bridge (optional) ──

    def _maybe_embed(self, text: str) -> tuple[Any, str]:
        """Embed text via eling's embeddings layer if available; else (None, '').

        Uses the low-level primitives of ``eling.layers.embeddings`` so the
        Continuum tier needs no EmbeddingIndex instance of its own. Gracefully
        degrades to FTS5-only search when no embedder is configured/available.
        """
        try:
            from ..layers import embeddings as emb

            # Local sentence-transformers model first (if eling[embeddings] installed)
            model = emb._get_model() if emb._HAS_SENTENCE_TRANSFORMERS else None
            if model is not None:
                vec = model.encode(text, normalize_embeddings=True).tolist()
                return pickle.dumps(vec), getattr(
                    model, "model_name", "sentence-transformers"
                )
            # Fall back to Mistral-compatible API (no heavy deps)
            api_key = emb._get_env_key()
            if api_key:
                res = emb._api_embed([text], api_key=api_key)
                if res:
                    return pickle.dumps(res[0]), emb._DEFAULT_EMBED_MODEL
        except Exception as exc:  # pragma: no cover - optional path
            logger.debug("embedding skipped: %s", exc)
        return None, ""

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            logger.debug("db connection close failed (non-fatal)")

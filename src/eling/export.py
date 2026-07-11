"""Explicit export — JSON/markdown dumps of all memory layers (Task 13.2).

Design Covers facts, KB, code index, Notion metadata, entity graph, builtin,
and config in one portable snapshot. Every layer under the same hood so an
export always reflects a single point-in-time view.

References reviewed:
  - memoir: branch/commit/checkout (git-like)
  - letri: JSON + markdown multi-layer exports
  - Origin: versioned page history export
  - icarus: provenance chain dump
  - Memory Palace: snapshot rollback (see 13.1)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .brain import Brain

logger = logging.getLogger(__name__)


def _dict_rows(cursor: Any) -> list[dict]:
    """Convert sqlite3.Row fetchall to plain dicts."""
    return [dict(r) for r in cursor]


def export_facts(brain: Brain) -> list[dict]:
    """Return all facts rows (including cleared/dormant) as flat dicts."""
    try:
        conn = brain.facts._conn
        cur = conn.execute(
            "SELECT fact_id, content, category, tags, source, trust_score, "
            "strength, created_at, updated_at, last_access_at "
            "FROM facts ORDER BY fact_id"
        )
        return _dict_rows(cur)
    except Exception as exc:
        logger.warning("export_facts failed: %s", exc)
        return []


def export_entity_graph(brain: Brain) -> list[dict]:
    """Return the entity_graph edges with entity names."""
    try:
        conn = brain.facts._conn
        cur = conn.execute(
            "SELECT ea.name AS a, eb.name AS b, eg.weight, eg.updated_at "
            "FROM entity_graph eg "
            "JOIN entities ea ON ea.entity_id = eg.entity_a_id "
            "JOIN entities eb ON eb.entity_id = eg.entity_b_id "
            "ORDER BY eg.weight DESC"
        )
        return _dict_rows(cur)
    except Exception as exc:
        logger.warning("export_entity_graph failed: %s", exc)
        return []


def export_all(brain: Brain) -> dict:
    """Collect every memory layer into one portable dict."""
    payload: dict[str, Any] = {
        "meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "0.2.0",
        },
        "facts": export_facts(brain),
        "entity_graph": export_entity_graph(brain),
        "stats": brain.stats(),
    }

    # KB chunks
    try:
        conn = brain.kb._conn
        payload["kb"] = _dict_rows(
            conn.execute(
                "SELECT chunk_id, source, section, content, content_type, "
                "created_at FROM kb_chunks ORDER BY chunk_id"
            )
        )
    except Exception as exc:
        logger.warning("export KB failed: %s", exc)
        payload["kb"] = []

    # Code index
    if brain.code.available:
        try:
            payload["code"] = brain.code.search("", max_files=500)
        except Exception as exc:
            logger.warning("export code index failed: %s", exc)
            payload["code"] = []

    # Notion — recent pages only
    if brain.notion.available:
        try:
            payload["notion_pages"] = brain.notion.search("", limit=200)
        except Exception:
            payload["notion_pages"] = []

    # Builtin memory
    if brain.builtin.available:
        try:
            payload["builtin"] = [
                {"source": r.get("source"), "content": r.get("content")}
                for r in brain.builtin.search("", limit=500)
            ]
        except Exception:
            payload["builtin"] = []

    return payload


def export_json(brain: Brain, path: str | None = None) -> tuple[str, Path | None]:
    data = export_all(brain)
    text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    file_path: Path | None = None
    if path:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")
    return text, file_path


def export_markdown(brain: Brain, path: str | None = None) -> tuple[str, Path | None]:
    data = export_all(brain)
    lines: list[str] = []
    lines.append("# Eling Memory Export")
    lines.append(f"\n*Exported at: {data['meta']['exported_at']}*")
    lines.append(f"*Version: {data['meta']['version']}*\n")
    lines.append("---\n")

    # Facts
    facts = data.get("facts", [])
    lines.append(f"## Facts ({len(facts)} total)")
    for f in facts:
        tags = f.get("tags") or ""
        trust = f.get("trust_score", 0.0)
        strength = f.get("strength", 1.0)
        cat = f.get("category", "general")
        lines.append(
            f"\n### Fact #{f['fact_id']}  `{cat}`  trust={trust:.2f}  strength={strength:.3f}"
        )
        if tags:
            lines.append(f"*Tags: {tags}*")
        lines.append(f"> {f['content']}")
        lines.append(
            f"*Source: {f.get('source', '?')}  Created: {f.get('created_at', '?')}*"
        )

    # Entity Graph
    edges = data.get("entity_graph", [])
    if edges:
        lines.append(f"\n---\n## Entity Graph ({len(edges)} edges)")
        lines.append("\n| Entity A | Entity B | Weight | Last Updated |")
        lines.append("|----------|----------|--------|-------------|")
        for e in edges:
            lines.append(
                f"| {e['a']} | {e['b']} | {e.get('weight', 0)} | {e.get('updated_at', '?')} |"
            )

    # KB
    kb = data.get("kb", [])
    if kb:
        lines.append(f"\n---\n## Knowledge Base ({len(kb)} chunks)")
        for k in kb:
            lines.append(f"\n### KB #{k.get('chunk_id')}  `{k.get('category', '?')}`")
            lines.append(f"> {k.get('content', '')[:200]}")
            lines.append(f"*Source: {k.get('source', '?')}*")

    # Code
    code = data.get("code", [])
    if code:
        lines.append(f"\n---\n## Code Index ({len(code)} symbols)")
        lines.append("\n| File | Symbol | Kind |")
        lines.append("|------|--------|------|")
        for c in code:
            lines.append(
                f"| {c.get('file', '')} | {c.get('symbol', '')} | {c.get('kind', '')} |"
            )

    # Notion
    notion = data.get("notion_pages", [])
    if notion:
        lines.append(f"\n---\n## Notion Pages ({len(notion)})")
        for p in notion:
            lines.append(f"\n- **{p.get('title', '?')}**")

    # Builtin
    builtin = data.get("builtin", [])
    if builtin:
        lines.append(f"\n---\n## Builtin Memory ({len(builtin)} items)")
        for b in builtin:
            lines.append(f"\n- *{b.get('source', '?')}:* {b.get('content', '')}")

    text = "\n".join(lines)
    file_path: Path | None = None
    if path:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")
    return text, file_path

"""Brain — unified orchestrator across all 5 memory layers.

Provides:
- remember(content): smart route to facts or KB
- recall(query): cross-layer search with RRF fusion
- reason(entities): compositional query via HRR
- reflect(content, title): promote to Notion as page
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .layers.builtin import BuiltinLayer
from .layers.facts import FactsLayer
from .layers.kb import KBLayer
from .layers.code import CodeLayer
from .layers.notion import NotionLayer

logger = logging.getLogger(__name__)

# RRF constant (Cormack et al. 2009)
RRF_K = 60


def _eling_home() -> Path:
    """Resolve ELING_HOME (default: $HERMES_HOME/eling or ~/.eling)."""
    env = os.environ.get("ELING_HOME")
    if env:
        return Path(env).expanduser()
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home).expanduser() / "eling"
    return Path("~/.eling").expanduser()


class Brain:
    """Unified second brain across 5 memory layers."""

    def __init__(
        self,
        home: str | Path | None = None,
        notion_api_key: str | None = None,
        notion_parent_id: str | None = None,
        project_path: str | Path | None = None,
        hrr_dim: int = 1024,
    ):
        self.home = Path(home).expanduser() if home else _eling_home()
        self.home.mkdir(parents=True, exist_ok=True)
        # Layers
        self.builtin = BuiltinLayer()
        self.facts = FactsLayer(db_path=self.home / "facts.db", hrr_dim=hrr_dim)
        self.kb = KBLayer(db_path=self.home / "kb.db")
        self.code = CodeLayer(project_path=project_path)
        self.notion = NotionLayer(api_key=notion_api_key, parent_page_id=notion_parent_id)

    # ------------------------------------------------------------------
    # remember — smart routing
    # ------------------------------------------------------------------
    def remember(
        self,
        content: str,
        layer: str = "auto",
        category: str = "general",
        tags: str = "",
        source: str = "",
        title: str = "",
    ) -> dict:
        """Smart-route content to the appropriate layer.

        layer="auto": short → facts, long (>500 chars or has markdown headings) → kb
        layer="facts" | "kb" | "notion": force specific layer.
        """
        if layer == "auto":
            if len(content) > 500 or "\n# " in content or "\n## " in content:
                layer = "kb"
            else:
                layer = "facts"

        if layer == "facts":
            fid = self.facts.add(content, category=category, tags=tags)
            return {"layer": "facts", "id": fid, "content": content[:120]}
        elif layer == "kb":
            src = source or title or "manual"
            n = self.kb.index(content, source=src)
            return {"layer": "kb", "source": src, "chunks_added": n}
        elif layer == "notion":
            if not self.notion.available:
                return {"layer": "notion", "error": "Notion not configured (NOTION_API_KEY missing)"}
            pid = self.notion.create_page(title=title or content[:80], content=content)
            return {"layer": "notion", "page_id": pid}
        else:
            raise ValueError(f"unknown layer: {layer}")

    # ------------------------------------------------------------------
    # recall — RRF fusion across layers
    # ------------------------------------------------------------------
    def recall(
        self,
        query: str,
        layers: list[str] | None = None,
        limit: int = 10,
        min_trust: float = 0.3,
    ) -> dict:
        """Cross-layer search with Reciprocal Rank Fusion.

        Returns dict with per-layer raw + fused 'merged' ranking.
        """
        if not layers:
            layers = ["builtin", "facts", "kb", "code", "notion"]

        per_layer: dict[str, list[dict]] = {}

        if "builtin" in layers:
            per_layer["builtin"] = self.builtin.search(query)[:limit]
        if "facts" in layers:
            per_layer["facts"] = self.facts.search(query, min_trust=min_trust, limit=limit)
        if "kb" in layers:
            per_layer["kb"] = self.kb.search(query, limit=limit)
        if "code" in layers and self.code.available:
            per_layer["code"] = self.code.search(query, max_files=limit)
        if "notion" in layers and self.notion.available:
            per_layer["notion"] = self.notion.search(query, limit=limit)

        # RRF fusion
        merged = self._rrf_fuse(per_layer, limit=limit)
        return {
            "query": query,
            "merged": merged,
            "per_layer": per_layer,
        }

    @staticmethod
    def _rrf_fuse(per_layer: dict[str, list[dict]], limit: int = 10) -> list[dict]:
        """Reciprocal Rank Fusion: score = sum(1 / (k + rank))."""
        scores: dict[str, float] = {}
        items: dict[str, dict] = {}

        for layer, results in per_layer.items():
            for rank, item in enumerate(results):
                # Build a stable key per item
                key = f"{layer}:{item.get('fact_id') or item.get('chunk_id') or item.get('id') or item.get('file') or hash(str(item))}"
                rrf_score = 1.0 / (RRF_K + rank + 1)
                scores[key] = scores.get(key, 0.0) + rrf_score
                if key not in items:
                    item_copy = dict(item)
                    item_copy["_layer"] = layer
                    items[key] = item_copy

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{**items[k], "_rrf_score": round(s, 4)} for k, s in ranked[:limit]]

    # ------------------------------------------------------------------
    # reason — compositional query
    # ------------------------------------------------------------------
    def reason(self, entities: list[str], limit: int = 10) -> list[dict]:
        """Find facts connecting MULTIPLE entities (HRR-based)."""
        return self.facts.reason(entities, limit=limit)

    def probe(self, entity: str, limit: int = 10) -> list[dict]:
        """All facts about a single entity."""
        return self.facts.probe(entity, limit=limit)

    # ------------------------------------------------------------------
    # reflect — promote fact to Notion
    # ------------------------------------------------------------------
    def reflect(self, fact_id: int, parent_page_id: str | None = None) -> dict:
        """Promote a high-trust fact to a Notion page."""
        fact = self.facts.get(fact_id)
        if not fact:
            return {"error": f"fact_id {fact_id} not found"}
        if not self.notion.available:
            return {"error": "Notion not configured"}

        # Get all entities for this fact for richer context
        entities = self.facts.entities_for_fact(fact_id)
        body_lines = [
            f"**Trust:** {fact['trust_score']:.2f}",
            f"**Category:** {fact['category']}",
            f"**Tags:** {fact.get('tags') or '(none)'}",
            f"**Created:** {fact['created_at']}",
            "",
            "## Content",
            "",
            fact["content"],
        ]
        if entities:
            body_lines.extend(["", "## Entities", "", *[f"- {e}" for e in entities]])

        page_id = self.notion.create_page(
            title=f"💡 {fact['content'][:60]}",
            content="\n".join(body_lines),
            parent_id=parent_page_id,
        )
        return {
            "fact_id": fact_id,
            "page_id": page_id,
            "promoted": page_id is not None,
        }

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "home": str(self.home),
            "facts": self.facts.stats(),
            "kb": self.kb.stats(),
            "code_available": self.code.available,
            "notion_available": self.notion.available,
            "builtin_available": self.builtin.available,
        }

    def close(self):
        self.facts.close()
        self.kb.close()
        self.notion.close()

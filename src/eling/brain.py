"""Brain — unified orchestrator across all 5 memory layers.

Provides:
- remember(content): smart route to facts or KB
- recall(query): cross-layer search with RRF fusion
- reason(entities): compositional query via HRR
- reflect(content, title): promote to Notion as page
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .layers.builtin import BuiltinLayer
from .layers.facts import FactsLayer
from .layers.kb import KBLayer
from .layers.code import CodeLayer
from .layers.notion import NotionLayer
from . import compress
from . import hooks as eling_hooks
from . import permissions
from .privacy import PrivacyPipeline, redact_kinds

logger = logging.getLogger(__name__)

# RRF constant (Cormack et al. 2009)
RRF_K = 60

# ── Notion child page routing ──────────────────────────────────────────────
# category → (child page icon + title, detection patterns)
NOTION_PAGES: dict[str, tuple[str, list[str]]] = {
    "project_summary": (
        "🎯 Project Summaries",
        [
            r"(?i)\b(project\s*(done|complete|finish|selesai))\b",
            r"(?i)\b(deploy.*success|release.*done|rollout)\b",
            r"(?i)\b(summary\s*(of|completion|final|akhir))\b",
        ],
    ),
    "credential": (
        "🔑 Credentials",
        [
            r"(?i)\b(api[_-]?key|apikey)\b",
            r"(?i)\b(password|passwd|secret|token|credential)\b",
            r"(?i)\b(ssh[_-]?key|access[_-]?key)\b",
        ],
    ),
    "address": (
        "📍 Addresses",
        [
            r"(?i)\b(alamat|address|domicile)\b",
            r"(?i)\b(located?\s+at|tinggal\s+(di|pada))\b",
        ],
    ),
    "config": (
        "⚙️ Configurations",
        [
            r"(?i)\b(config|configuration|setting|setup)\b",
            r"(?i)\b(environment\s*(var|config)|env.*config)\b",
        ],
    ),
}

# Default fallback page for uncategorised content
DEFAULT_NOTION_PAGE = "📋 Task Logs"


def _eling_home() -> Path:
    """Resolve ELING_HOME (default: $HERMES_HOME/eling or ~/.eling)."""
    env = os.environ.get("ELING_HOME")
    if env:
        return Path(env).expanduser()
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home).expanduser() / "eling"
    return Path("~/.eling").expanduser()


def _detect_notion_category(content: str, category_hint: str = "") -> str:
    """Auto-detect Notion child page category from content + optional hint.

    Explicit hint wins over detection.
    """
    if category_hint and category_hint != "general" and category_hint in NOTION_PAGES:
        return category_hint
    for cat, (_title, patterns) in NOTION_PAGES.items():
        for pat in patterns:
            if re.search(pat, content):
                return cat
    return "task_logs"  # default


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
        self.code = CodeLayer(project_path=project_path, auto_index=False)
        self.notion = NotionLayer(api_key=notion_api_key, parent_page_id=notion_parent_id)
        # Child page cache: category → page_id
        self._child_pages: dict[str, str] = {}
        self.privacy = PrivacyPipeline()
        # Hooks registry
        self.hooks = eling_hooks.HookRegistry()
        eling_hooks.register_default_hooks(self)

    def fire_hook(self, hook_name: str, **ctx: Any) -> list[Any]:
        """Fire a lifecycle hook. Context kwargs become the dict passed to handlers."""
        return self.hooks.fire(hook_name, ctx)

    # ── Notion child page auto-creation ────────────────────────────────────

    def _ensure_child_page(self, title: str) -> str | None:
        """Find or create a child page under the configured Notion parent.

        Results are cached in _child_pages so subsequent calls are instant.
        """
        if title in self._child_pages:
            return self._child_pages[title]
        if not self.notion.available:
            return None
        parent = self.notion.parent_page_id
        if not parent:
            return None
        for r in self.notion.search(title, limit=5):
            if r.get("title") == title:
                self._child_pages[title] = r["id"]
                return r["id"]
        pid = self.notion.create_page(
            title,
            f"_auto-managed by eling_",
            parent_id=parent,
        )
        if pid:
            self._child_pages[title] = pid
        return pid

    def _ensure_task_logs(self) -> str | None:
        """Backward-compat wrapper — ensure the 📋 Task Logs page exists."""
        return self._ensure_child_page("📋 Task Logs")

    def _route_parent(self, category: str) -> str | None:
        """Resolve the Notion parent for a given content category.

        Known categories → their dedicated child page.
        Unknown / 'task_logs' → 📋 Task Logs.
        """
        if category in NOTION_PAGES:
            _title, _ = NOTION_PAGES[category]
            return self._ensure_child_page(_title)
        return self._ensure_task_logs()

    # ── Snapshot / rollback (Task 13.1) ──

    def snapshot(self, reason: str = "") -> dict:
        """Snapshot the facts database before bulk operations."""
        from . import snapshot as snap_mod
        return snap_mod.create_snapshot(self.facts.db_path, reason=reason)

    def rollback(self, snapshot_id: str) -> dict:
        """Rollback the facts database to a named snapshot."""
        from . import snapshot as snap_mod
        return snap_mod.rollback(snapshot_id, self.facts.db_path)

    def list_snapshots(self) -> list[dict]:
        """List available snapshots."""
        from . import snapshot as snap_mod
        return snap_mod.list_snapshots(self.facts.db_path)

    # ------------------------------------------------------------------
    # remember — smart routing with privacy + compression
    # ------------------------------------------------------------------
    def remember(
        self,
        content: str,
        layer: str = "auto",
        category: str = "general",
        tags: str = "",
        source: str = "",
        title: str = "",
        skip_dedup: bool = False,
    ) -> dict:
        """Smart-route content to the appropriate layer.

        layer="auto": short → facts, long (>500 chars or has markdown headings) → kb
        layer="facts" | "kb" | "notion": force specific layer.

        When layer="notion", the category is used to route content to the
        appropriate Notion child page:
          - "credential"       → 🔑 Credentials
          - "config"           → ⚙️ Configurations
          - "address"          → 📍 Addresses
          - "project_summary"  → 🎯 Project Summaries
          - "general" (default) → auto-detect from content, else 📋 Task Logs

        Privacy & compression pipeline runs before storage:
        1. SHA-256 dedup (skip with skip_dedup=True)
        2. Secret/pii stripping
        3. Optional LLM compression
        """
        # ── Privacy + compression pipeline ──
        pp = self.privacy.process(content, skip_dedup=skip_dedup)
        if pp["is_duplicate"]:
            result = {
                "layer": "dedup",
                "is_duplicate": True,
                "message": "content already stored (SHA-256 dedup hit)",
                "redacted": pp["redacted"],
            }
            self.fire_hook(eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result)
            return result

        clean_content = pp["clean"]
        redacted = pp["redacted"]
        compressed = compress.compress(clean_content)

        meta = {"redacted": redacted}
        if compressed != clean_content:
            meta["compressed_from"] = len(clean_content)
            meta["compressed_to"] = len(compressed)

        # ── Permissions gate (Task 12.4) ──
        target_layer = layer
        if target_layer == "auto":
            target_layer = "kb" if (len(compressed) > 500 or "\n# " in compressed or "\n## " in compressed) else "facts"
        src_ident = source or "manual"
        if not permissions.check_access(src_ident, target_layer, "write"):
            return {
                "layer": target_layer,
                "error": f"permission denied: source '{src_ident}' cannot write to layer '{target_layer}'",
                "redacted": redacted,
            }

        if layer == "auto":
            if len(compressed) > 500 or "\n# " in compressed or "\n## " in compressed:
                layer = "kb"
            else:
                layer = "facts"

        if layer == "facts":
            fid = self.facts.add(compressed, category=category, tags=tags, source=source or "manual")
            result = {"layer": "facts", "id": fid, "content": compressed[:120], **meta}
            self.fire_hook(eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result)
            return result
        elif layer == "kb":
            src = source or title or "manual"
            n = self.kb.index(compressed, source=src)
            result = {"layer": "kb", "source": src, "chunks_added": n, **meta}
            self.fire_hook(eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result)
            return result
        elif layer == "notion":
            if not self.notion.available:
                result = {"layer": "notion", "error": "Notion not configured (NOTION_API_KEY missing)", **meta}
                self.fire_hook(eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result)
                return result
            # Auto-detect category from content if not explicitly set
            notion_cat = _detect_notion_category(compressed, category_hint=category)
            parent_id = self._route_parent(notion_cat)
            store = compressed if len(compressed) > 80 else content
            pid = self.notion.create_page(
                title=title or store[:80],
                content=store,
                parent_id=parent_id or self.notion.parent_page_id,
            )
            result = {"layer": "notion", "page_id": pid, "notion_category": notion_cat, **meta}
            self.fire_hook(eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result)
            return result
        else:
            raise ValueError(f"unknown layer: {layer}")

    # ------------------------------------------------------------------
    # recall — RRF fusion across layers
    # ------------------------------------------------------------------
    def recall(
        self,
        query: str,
        limit: int = 10,
        source: str = "",
        layers: str | list[str] | None = None,
    ) -> list[dict]:
        """Cross-layer search with Reciprocal Rank Fusion (BM25 + trigram + porter).

        Args:
            query: Search string.
            limit: Max merged results.
            source: Filter by agent source (empty = all agents).
            layers: Layers to search. None = all, or list like ["facts", "kb"].
        """
        if isinstance(layers, str):
            layers = [l.strip() for l in layers.split(",") if l.strip()]
        if not layers:
            layers = ["facts", "kb", "builtin", "code"]

        all_results: list[dict] = []
        layer_weights = {"facts": 1.0, "kb": 0.9, "builtin": 0.7, "code": 0.5}

        if "facts" in layers:
            res = self.facts.search(query, limit=limit, source=source)
            for r in res or []:
                r["_layer"] = "facts"
                r["_rank"] = 0
                all_results.append(r)

        if "kb" in layers:
            res = self.kb.search(query, limit=limit)
            for r in res or []:
                r["_layer"] = "kb"
                r["_rank"] = 0
                all_results.append(r)

        if "builtin" in layers:
            res = self.builtin.search(query, limit=limit)
            for r in res or []:
                r["_layer"] = "builtin"
                r["_rank"] = 0
                all_results.append(r)

        if "code" in layers:
            res = self.code.search(query, limit=limit)
            for r in res or []:
                r["_layer"] = "code"
                r["_rank"] = 0
                all_results.append(r)

        # RRF fusion — per-layer ranks assigned above (position in each list)
        # Assign rank based on list position
        for layer_name in layers:
            offset = 0
            for i, r in enumerate(all_results):
                if r.get("_layer") == layer_name:
                    r["_rank"] = i - offset
                else:
                    offset += 1

        # Re-rank by RRF score
        def _rrf_score(r: dict) -> float:
            k = 1.0
            score = 0.0
            layer = r.get("_layer", "")
            weight = layer_weights.get(layer, 0.5)
            score += weight / (RRF_K + r.get("_rank", 0))
            # Boost exact title match
            if query.lower() in (r.get("title", "") or "").lower():
                score += 0.1
            return score

        all_results.sort(key=_rrf_score, reverse=True)
        return all_results[:limit]

    # ------------------------------------------------------------------
    # reason — compositional HRR query
    # ------------------------------------------------------------------
    def reason(self, entities: list[str], limit: int = 10) -> list[dict]:
        """Find facts connecting multiple entities via compositional HRR query."""
        if not entities or len(entities) < 2:
            return {"error": "need at least 2 entities for reasoning"}
        # Query the facts layer — it has HRR support for multi-entity queries
        query = " ".join(entities)
        results = self.facts.search(query, limit=limit)
        if not results:
            return {"entities": entities, "connections": 0, "results": []}
        # Filter to results mentioning at least 2 of the entities
        filtered = []
        for r in results:
            content = r.get("content", "").lower()
            mentions = sum(1 for e in entities if e.lower() in content)
            if mentions >= 2:
                filtered.append(r)
        return {
            "entities": entities,
            "connections": len(filtered),
            "results": filtered,
        }

    # ------------------------------------------------------------------
    # probe — get all facts about an entity
    # ------------------------------------------------------------------
    def probe(self, entity: str, limit: int = 10) -> list[dict]:
        """Get all facts about a single entity via BM25 + trigram search."""
        return self.facts.search(entity, limit=limit)

    # ------------------------------------------------------------------
    # reflect — promote a high-trust fact to Notion
    # ------------------------------------------------------------------
    def reflect(self, fact_id: int, parent_page_id: str | None = None) -> dict:
        """Promote a high-trust fact to a Notion page.

        The fact is routed under the appropriate child page based on its
        category (project_summary → 🎯 Project Summaries, credential → 🔑
        Credentials, etc.). Uncategorised facts go to 📋 Task Logs.
        """
        fact = self.facts.get(fact_id)
        if not fact:
            return {"error": f"fact {fact_id} not found", "fact_id": fact_id, "promoted": False}
        if not self.notion.available:
            return {"error": "Notion not configured", "fact_id": fact_id, "promoted": False}

        # Resolve effective parent
        effective_parent = parent_page_id
        if not effective_parent:
            fact_cat = fact.get("category", "")
            notion_cat = _detect_notion_category(fact["content"], category_hint=fact_cat)
            effective_parent = self._route_parent(notion_cat) or self.notion.parent_page_id
        if not effective_parent:
            return {"error": "no parent page available for reflect", "fact_id": fact_id, "promoted": False}

        # Build rich page with metadata
        entities = self.facts.entities_for_fact(fact_id) or []
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
            parent_id=effective_parent,
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
        }

    # ------------------------------------------------------------------
    # think — synthesis + gap analysis
    # ------------------------------------------------------------------
    def think(
        self,
        query: str,
        entities: list[str] | None = None,
        limit: int = 10,
    ) -> dict:
        """Synthesis + gap-analysis across layers.

        Runs recall + reason, then constructs stale/contradicted/unknown
        analysis from existing facts.
        """
        # Recall
        recall_results = self.recall(query, limit=limit)
        # Reason
        reason_results: dict | list = {}
        if entities and len(entities) >= 2:
            reason_results = self.reason(entities, limit=limit)
        # Basic gap analysis
        found_count = len(recall_results)
        return {
            "query": query,
            "entities": entities or [],
            "recall_count": found_count,
            "recall_results": recall_results,
            "reason": reason_results,
            "gaps": (
                []
                if found_count >= limit
                else ["Limited results — consider expanding query"]
            ),
        }

    # ------------------------------------------------------------------
    # export — full brain snapshot
    # ------------------------------------------------------------------
    def export(self, format: str = "json", path: str | None = None) -> dict:
        """Export all memory layers as JSON or Markdown."""
        from .export import export_brain
        return export_brain(self, format=format, path=path)

    # ------------------------------------------------------------------
    # sync — bidirectional layer sync + Notion push
    # ------------------------------------------------------------------
    def sync(
        self,
        direction: str = "auto",
        layer: str = "auto",
        fact_ids: list[int] | None = None,
    ) -> dict:
        """Synchronize between memory layers.

        direction="push"     → facts → Notion
        direction="pull"     → Notion → KB (future)
        direction="flush"    → flush SQLite WAL to disk
        direction="auto/all" → both directions

        layer="facts" | "notion" | "kb" : scope the sync
        fact_ids: explicitly promote specific facts to Notion
        """
        result: dict[str, Any] = {"direction": direction, "layer": layer}

        # Flush SQLite WAL to disk
        if direction in ("flush", "all", "auto") and layer in ("auto", "facts", "kb"):
            flushed = []
            if self.facts:
                flushed.append("facts")
            if self.kb:
                flushed.append("kb")
            result["flushed"] = flushed

        # Push facts to Notion
        if direction in ("push", "all", "auto") and layer in ("auto", "facts", "notion"):
            if not self.notion.available:
                result["notion_note"] = "Notion unavailable — skip push"
                if direction != "auto":
                    return result
            else:
                if fact_ids:
                    # Promote specific facts
                    promoted = []
                    for fid in fact_ids:
                        r = self.reflect(fid)
                        if r.get("promoted"):
                            promoted.append(fid)
                    result["promoted_facts"] = promoted
                else:
                    # Auto-promote high-trust facts not yet in Notion
                    high_trust = self.facts.search("", min_trust=0.9, limit=20)
                    promoted = []
                    for fact in high_trust or []:
                        fid = fact.get("fact_id")
                        if fid:
                            r = self.reflect(fid)
                            if r.get("promoted"):
                                promoted.append(fid)
                    result["promoted_facts"] = promoted
                result["promoted_count"] = len(result.get("promoted_facts", []))

        # Pull Notion → KB (stub for future)
        if direction in ("pull", "all") and layer in ("auto", "notion", "kb"):
            result["pull"] = "not yet implemented"

        return result

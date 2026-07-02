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
        self.code = CodeLayer(project_path=project_path, auto_index=False)
        self.notion = NotionLayer(api_key=notion_api_key, parent_page_id=notion_parent_id)
        self._task_logs_id: str | None = None
        self.privacy = PrivacyPipeline()
        # Hooks registry
        self.hooks = eling_hooks.HookRegistry()
        eling_hooks.register_default_hooks(self)

    def fire_hook(self, hook_name: str, **ctx: Any) -> list[Any]:
        """Fire a lifecycle hook. Context kwargs become the dict passed to handlers."""
        return self.hooks.fire(hook_name, ctx)

    # ── Task Logs auto-creation (Notion Tier 5) ──

    def _ensure_task_logs(self) -> str | None:
        """Auto-create '📋 Task Logs' child page under configured parent.

        Once created, caches the page ID so all future reflects go into it.
        Returns the Task Logs page ID, or None if Notion is not available.
        """
        if self._task_logs_id:
            return self._task_logs_id
        if not self.notion.available:
            return None
        parent = self.notion.parent_page_id
        if not parent:
            return None
        # Search for existing Task Logs page
        for r in self.notion.search("📋 Task Logs", limit=5):
            if r.get("title") == "📋 Task Logs":
                self._task_logs_id = r["id"]
                return self._task_logs_id
        # Create it
        pid = self.notion.create_page(
            "📋 Task Logs",
            "All reflected facts from Eling brain\n\n---\n_auto-managed by eling_",
            parent_id=parent,
        )
        if pid:
            self._task_logs_id = pid
        return pid

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
            store = compressed if len(compressed) > 80 else content
            pid = self.notion.create_page(
                title=title or store[:80],
                content=store,
                parent_id=self._ensure_task_logs() or self.notion.parent_page_id,
            )
            result = {"layer": "notion", "page_id": pid, **meta}
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
        layers: list[str] | None = None,
        limit: int = 10,
        min_trust: float = 0.3,
        source: str | None = None,
    ) -> dict:
        """Cross-layer search with Reciprocal Rank Fusion.

        Optional `source` limits results to one agent origin (hermes, opencode, etc.).
        """
        self.fire_hook(eling_hooks.HOOK_PRE_TOOL_USE, tool_name="recall", arguments=query)

        if not layers:
            layers = ["builtin", "facts", "kb", "code", "notion"]

        per_layer: dict[str, list[dict]] = {}

        if "builtin" in layers:
            per_layer["builtin"] = self.builtin.search(query)[:limit]
        if "facts" in layers:
            per_layer["facts"] = self.facts.search(query, min_trust=min_trust, source=source, limit=limit)
        if "kb" in layers:
            per_layer["kb"] = self.kb.search(query, source=source, limit=limit)
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

    # ── think — synthesis + gap-analysis (Task 12.5) ──

    @staticmethod
    def _think_content(item: dict) -> str:
        """Extract human-readable content from any layer's RRF result item."""
        layer = item.get("_layer", "")
        if layer == "code":
            return f"{item.get('file','')}::{item.get('symbol','')} ({item.get('kind','')})"
        if layer == "notion":
            return item.get("title", item.get("id", ""))
        if layer == "builtin":
            return item.get("content", str(item.get("source", "")))
        # facts, kb
        return item.get("content", json.dumps(item, default=str))

    def think(
        self,
        query: str,
        entities: list[str] | None = None,
        limit: int = 10,
    ) -> dict:
        """Synthesis + gap-analysis: recall + reason, then report stale/contradicted/unknown.

        This is the expensive path — kept behind an explicit tool call so the
        cheap ``eling_recall`` path stays unchanged.

        Returns
        -------
        dict with:
          query, synthesis (summary),
          results (merged recall),
          gap_analysis { stale_count, stale_facts, contradicted_count,
                         contradicted_facts, unknown_count }
        """
        # Empty-query short-circuit: return immediately
        if not query or not query.strip():
            return {
                "query": query,
                "synthesis": "No query provided.",
                "results": [],
                "reason_results": [],
                "gap_analysis": {
                    "stale_count": 0, "stale_facts": [],
                    "contradicted_count": 0, "contradicted_facts": [],
                    "unknown_count": 1,
                },
            }

        # 1. Raw recall (cheap, unchanged)
        recall_result = self.recall(query, limit=limit)
        merged = recall_result.get("merged", [])

        # 2. Reason if entities provided (compositional)
        reason_results: list[dict] = []
        if entities:
            reason_results = self.reason(entities, limit=limit)

        # 3. Gap analysis — scan recall results for stale / contradicted
        from . import decay
        ACTIVE = decay.ACTIVE_THRESHOLD
        stale: list[dict] = []
        contradicted: list[dict] = []

        for fact in merged:
            strength = fact.get("strength", 1.0)
            tags = fact.get("tags") or ""
            if isinstance(strength, (int, float)) and strength < ACTIVE:
                stale.append({
                    "fact_id": fact.get("fact_id"),
                    "content": self._think_content(fact),
                    "strength": round(strength, 3),
                    "source": fact.get("source"),
                })
            if "contradiction_pending" in tags:
                contradicted.append({
                    "fact_id": fact.get("fact_id"),
                    "content": self._think_content(fact),
                    "tags": tags,
                })

        # Also check reason results for stale/contradicted
        seen_ids = {f.get("fact_id") for f in merged}
        for fact in reason_results:
            if fact.get("fact_id") in seen_ids:
                continue
            strength = fact.get("strength", 1.0)
            tags = fact.get("tags") or ""
            if isinstance(strength, (int, float)) and strength < ACTIVE:
                stale.append({
                    "fact_id": fact.get("fact_id"),
                    "content": self._think_content(fact),
                    "strength": round(strength, 3),
                    "source": fact.get("source"),
                })
            if "contradiction_pending" in tags:
                contradicted.append({
                    "fact_id": fact.get("fact_id"),
                    "content": self._think_content(fact),
                    "tags": tags,
                })
            seen_ids.add(fact.get("fact_id"))

        unknown_count = 0 if merged else 1  # no results = unknown topic

        # Programmatic synthesis
        parts = []
        n_facts = len(merged)
        n_layers = len(recall_result.get("per_layer", {}))
        if n_facts:
            parts.append(f"Found {n_facts} result{'s' if n_facts != 1 else ''} across {n_layers} layer{'s' if n_layers != 1 else ''}.")
        else:
            parts.append("No relevant facts found — this appears to be new/unexplored information.")
        if stale:
            parts.append(f"{len(stale)} fact{'s' if len(stale) != 1 else ''} {'are' if len(stale) != 1 else 'is'} stale (strength < {ACTIVE}).")
        if contradicted:
            parts.append(f"{len(contradicted)} fact{'s' if len(contradicted) != 1 else ''} {'are' if len(contradicted) != 1 else 'is'} flagged as contradicted.")
        if entities:
            parts.append(f"Reasoned across {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}: {', '.join(entities)}.")

        return {
            "query": query,
            "synthesis": " ".join(parts),
            "results": merged,
            "reason_results": reason_results,
            "gap_analysis": {
                "stale_count": len(stale),
                "stale_facts": stale[:5],
                "contradicted_count": len(contradicted),
                "contradicted_facts": contradicted[:5],
                "unknown_count": unknown_count,
            },
        }

    # ── export — dump all layers (Task 13.2) ──

    def export(self, format: str = "json", path: str | None = None) -> dict:
        """Export all memory layers. format='json' or 'markdown'."""
        from .export import export_json, export_markdown

        if format == "markdown":
            text, file_path = export_markdown(self, path)
        else:
            text, file_path = export_json(self, path)

        return {
            "format": format,
            "bytes": len(text),
            "path": str(file_path) if file_path else None,
            "preview": text[:500],
        }

    # ------------------------------------------------------------------
    # reflect — promote fact to Notion
    # ------------------------------------------------------------------
    def reflect(self, fact_id: int, parent_page_id: str | None = None) -> dict:
        """Promote a high-trust fact to a Notion page.

        Facts are auto-routed under '📋 Task Logs' — a child page created
        automatically under the configured parent. Pass explicit parent_page_id
        to bypass this routing.
        """
        fact = self.facts.get(fact_id)
        if not fact:
            return {"error": f"fact_id {fact_id} not found"}
        
        # Detailed configuration check
        missing = []
        if not self.notion._has_httpx():
            missing.append("httpx library (pip install eling[notion])")
        if not self.notion.api_key:
            missing.append("NOTION_API_KEY environment variable")
        if not (parent_page_id or self.notion.parent_page_id):
            missing.append("parent_page_id or NOTION_PARENT_PAGE_ID")
        if missing:
            return {
                "error": f"Notion not configured. Missing: {'; '.join(missing)}",
                "fact_id": fact_id,
                "promoted": False,
            }

        # Resolve effective parent: explicit > Task Logs > configured parent
        effective_parent = parent_page_id
        if not effective_parent:
            effective_parent = self._ensure_task_logs() or self.notion.parent_page_id
        if not effective_parent:
            return {"error": "no parent page available for reflect", "promoted": False} 

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
            "builtin_available": self.builtin.available,
            "privacy": self.privacy.stats(),
            "hooks": {
                "total_handlers": self.hooks.total_handlers,
                "hooks_with_handlers": sum(1 for h in eling_hooks.ALL_HOOKS if self.hooks.has_handlers(h)),
            },
        }

    def close(self):
        self.facts.close()
        self.kb.close()
        self.notion.close()

    # ------------------------------------------------------------------
    # sync — layer synchronization
    # ------------------------------------------------------------------
    def sync(
        self,
        direction: str = "push",
        layer: str = "auto",
        sync_state_path: str | None = None,
    ) -> dict:
        """Synchronize data between layers.

        direction="push":  facts → Notion (high-trust facts promoted)
        direction="pull":  Notion → KB (recent pages pulled locally)
        direction="flush": ensure all pending writes to disk
        direction="all":   push + flush (default)

        layer="auto": operates on all available layers.
        layer="facts"|"notion"|"kb": limit to one layer pair.

        Returns summary dict with counts per operation.
        """
        result: dict = {
            "pushed": 0,
            "pulled": 0,
            "errors": [],
            "layers": {},
        }

        # ── Fire sync_start hook ──
        self._fire_hook("sync_start", {
            "direction": direction,
            "layer": layer,
        })

        try:
            # ── flush: persist pending writes ──
            if direction in ("flush", "all", "auto"):
                self.facts.flush()
                self.kb.flush()
                result["layers"]["facts_flushed"] = True
                result["layers"]["kb_flushed"] = True

            # ── push: facts → Notion ──
            if direction in ("push", "all", "auto") and layer in ("auto", "facts", "notion"):
                if self.notion.available:
                    try:
                        pushed = self._sync_push_facts()
                        result["pushed"] = pushed
                        result["layers"]["facts_to_notion"] = pushed
                    except Exception as e:
                        result["errors"].append(f"push failed: {e}")
                else:
                    result["layers"]["facts_to_notion"] = 0
                    result["layers"]["notion_note"] = "Notion unavailable (no API key)"

            # ── pull: Notion → KB ──
            if direction in ("pull", "all") and layer in ("auto", "notion", "kb"):
                if self.notion.available:
                    try:
                        pulled = self._sync_pull_notion()
                        result["pulled"] = pulled
                        result["layers"]["notion_to_kb"] = pulled
                    except Exception as e:
                        result["errors"].append(f"pull failed: {e}")
                else:
                    result["layers"]["notion_to_kb"] = 0
                    result["layers"]["notion_note"] = "Notion unavailable (no API key)"

            # ── state tracking ──
            if sync_state_path:
                from pathlib import Path
                state_path = Path(sync_state_path)
                state: dict = {}
                if state_path.exists():
                    try:
                        state = json.loads(state_path.read_text())
                    except Exception:
                        pass
                state["last_sync"] = __import__("datetime").datetime.now().isoformat()
                state.setdefault("total_pushed", 0)
                state["total_pushed"] += result["pushed"]
                state.setdefault("total_pulled", 0)
                state["total_pulled"] += result["pulled"]
                state.setdefault("errors", [])
                if result["errors"]:
                    state["errors"].extend(result["errors"][-5:])  # keep last 5
                state_path.write_text(json.dumps(state, indent=2) + "\n")

            # ── Fire sync_complete hook ──
            self._fire_hook("sync_complete", {
                "direction": direction,
                "layer": layer,
                "result": result,
            })

        except Exception as e:
            self._fire_hook("sync_error", {
                "direction": direction,
                "layer": layer,
                "error": str(e),
            })
            raise

        return result

    def _fire_hook(self, hook_name: str, ctx: dict) -> list:
        """Fire a hook via the hook registry."""
        return self.hooks.fire(hook_name, ctx)

    def _sync_push_facts(self) -> int:
        """Push high-trust facts as Notion pages. Returns count."""
        import hashlib

        pushed = 0
        all_facts = self.facts.list_all()
        # Track synced fact hashes locally to avoid duplicates
        synced_path = self.home / ".sync_push_cache.json"
        synced: set[str] = set()
        if synced_path.exists():
            try:
                synced = set(json.loads(synced_path.read_text()))
            except Exception:
                pass

        for f in all_facts:
            fact_id = f.get("id", f.get("fact_id"))
            content = f.get("content", "")
            trust = f.get("trust_score", f.get("trust", 0.5))
            if not content or trust < 0.7:
                continue  # only promote high-trust facts
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            if content_hash in synced:
                continue
            title = f.get("title", "") or content[:80].split("\n")[0]
            tags = f.get("tags", "")
            body = content
            if tags:
                body = f"**Tags:** {tags}\n\n{content}"
            page_id = self.notion.create_page(title=title[:200], content=body[:1800])
            if page_id:
                synced.add(content_hash)
                pushed += 1

        synced_path.write_text(json.dumps(sorted(synced), indent=2))
        return pushed

    def _sync_pull_notion(self) -> int:
        """Pull recent Notion pages into KB. Returns count."""
        pulled = 0
        try:
            # Search for recent pages in the parent
            if not self.notion.parent_page_id:
                return 0
            pages = self.notion.search("", limit=50)
            for p in pages:
                title = p.get("title", "")
                url = p.get("url", "")
                page_id = p["id"]
                # Skip if already in KB (check by source URL)
                existing = self.kb.search(f"notion:{page_id}", limit=1)
                if any("notion:" + page_id in str(r.get("source", "")) for r in existing):
                    continue
                md = self.notion.get_page_markdown(page_id)
                if md:
                    source = f"notion:{page_id}"
                    meta = f"Title: {title}\nURL: {url}\n"
                    self.kb.index(source=source, content=meta + md[:4000])
                    pulled += 1
        except Exception:
            pass
        return pulled

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
        self.code = CodeLayer(project_path=project_path)
        self.notion = NotionLayer(api_key=notion_api_key, parent_page_id=notion_parent_id)
        self.privacy = PrivacyPipeline()
        # Hooks registry
        self.hooks = eling_hooks.HookRegistry()
        eling_hooks.register_default_hooks(self)

    def fire_hook(self, hook_name: str, **ctx: Any) -> list[Any]:
        """Fire a lifecycle hook. Context kwargs become the dict passed to handlers."""
        return self.hooks.fire(hook_name, ctx)

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
            pid = self.notion.create_page(title=title or store[:80], content=store)
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
    ) -> dict:
        """Cross-layer search with Reciprocal Rank Fusion.

        Returns dict with per-layer raw + fused 'merged' ranking.
        """
        self.fire_hook(eling_hooks.HOOK_PRE_TOOL_USE, tool_name="recall", arguments=query)

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

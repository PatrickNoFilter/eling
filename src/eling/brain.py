"""Brain — unified orchestrator across all 6 memory layers.

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
from .layers.obsidian import ObsidianLayer
from . import compress
from . import hooks as eling_hooks
from . import permissions
from .privacy import PrivacyPipeline

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
    """Unified second brain across 6 memory layers."""

    def __init__(
        self,
        home: str | Path | None = None,
        notion_api_key: str | None = None,
        notion_parent_id: str | None = None,
        obsidian_vault_path: str | Path | None = None,
        project_path: str | Path | None = None,
        hrr_dim: int = 1024,
        adapter: str | None = None,
        embedding_model: str = "",
    ):
        self.home = Path(home).expanduser() if home else _eling_home()
        self.home.mkdir(parents=True, exist_ok=True)
        # Layers
        self.builtin = BuiltinLayer()
        self.facts = FactsLayer(
            db_path=self.home / "facts.db",
            hrr_dim=hrr_dim,
            embedding_model=embedding_model,
        )
        self.kb = KBLayer(db_path=self.home / "kb.db")
        self.code = CodeLayer(project_path=project_path, auto_index=False)
        self.obsidian = ObsidianLayer(vault_path=obsidian_vault_path)
        self.notion = NotionLayer(
            api_key=notion_api_key, parent_page_id=notion_parent_id
        )
        # Child page cache: title → page_id (includes task_logs)
        self._child_pages: dict[str, str] = {}
        self.privacy = PrivacyPipeline()
        # Hooks registry
        self.hooks = eling_hooks.HookRegistry()
        eling_hooks.register_default_hooks(self)
        # Adapter for verify-on-stop (hermes | opencode | openclaw | auto)
        self._adapter: str = adapter or "auto"
        # Project path (used by verify-on-stop → spec-kit)
        self._project_path: Path | None = (
            Path(project_path).expanduser().resolve() if project_path else None
        )
        if self._project_path:
            from . import verify_on_stop as vos

            vos.set_project_path(self._project_path)

    @property
    def _task_logs_id(self) -> str | None:
        """Backward-compat: _ensure_task_logs() caches into _child_pages."""
        return self._child_pages.get("📋 Task Logs")

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
            "_auto-managed by eling_",
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
            title, _ = NOTION_PAGES[category]
            return self._ensure_child_page(title)
        return self._ensure_task_logs()

    # ── Snapshot / rollback (Task 13.1) ──

    def snapshot(self, reason: str = "") -> dict:
        """Snapshot the facts database before bulk operations."""
        from . import snapshot as snap_mod

        return snap_mod.create_snapshot(self.facts.db_path, reason=reason)

    def rollback(self, snapshot_id: str) -> dict:
        """Rollback the facts database to a named snapshot.

        The current facts layer is closed and re-initialized from the
        restored database so the in-memory state is consistent.
        """
        from . import snapshot as snap_mod

        result = snap_mod.rollback(snapshot_id, self.facts.db_path)
        # Re-initialize facts layer from the restored DB
        old = self.facts
        old.close()
        self.facts = FactsLayer(
            db_path=old.db_path,
            hrr_dim=old.hrr_dim,
            embedding_model=old.embedding_model,
        )
        return result

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
            self.fire_hook(
                eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result
            )
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
            target_layer = (
                "kb"
                if (
                    len(compressed) > 500
                    or "\n# " in compressed
                    or "\n## " in compressed
                )
                else "facts"
            )
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
            fid = self.facts.add(
                compressed, category=category, tags=tags, source=source or "manual"
            )
            result = {"layer": "facts", "id": fid, "content": compressed[:120], **meta}
            self.fire_hook(
                eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result
            )
            return result
        elif layer == "kb":
            src = source or title or "manual"
            n = self.kb.index(compressed, source=src)
            result = {"layer": "kb", "source": src, "chunks_added": n, **meta}
            self.fire_hook(
                eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result
            )
            return result
        elif layer == "notion":
            if not self.notion.available:
                result = {
                    "layer": "notion",
                    "error": "Notion not configured (NOTION_API_KEY missing)",
                    **meta,
                }
                self.fire_hook(
                    eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result
                )
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
            result = {
                "layer": "notion",
                "page_id": pid,
                "notion_category": notion_cat,
                **meta,
            }
            self.fire_hook(
                eling_hooks.HOOK_POST_TOOL_USE, tool_name="remember", result=result
            )
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
        self.fire_hook(
            eling_hooks.HOOK_PRE_TOOL_USE, tool_name="recall", arguments=query
        )

        if not layers:
            layers = ["builtin", "facts", "kb", "code", "notion"]

        per_layer: dict[str, list[dict]] = {}

        if "builtin" in layers:
            per_layer["builtin"] = self.builtin.search(query)[:limit]
        if "facts" in layers:
            per_layer["facts"] = self.facts.search(
                query, min_trust=min_trust, source=source, limit=limit
            )
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
            return f"{item.get('file', '')}::{item.get('symbol', '')} ({item.get('kind', '')})"
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
                    "stale_count": 0,
                    "stale_facts": [],
                    "contradicted_count": 0,
                    "contradicted_facts": [],
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
                stale.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "content": self._think_content(fact),
                        "strength": round(strength, 3),
                        "source": fact.get("source"),
                    }
                )
            if "contradiction_pending" in tags:
                contradicted.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "content": self._think_content(fact),
                        "tags": tags,
                    }
                )

        # Also check reason results for stale/contradicted
        seen_ids = {f.get("fact_id") for f in merged}
        for fact in reason_results:
            if fact.get("fact_id") in seen_ids:
                continue
            strength = fact.get("strength", 1.0)
            tags = fact.get("tags") or ""
            if isinstance(strength, (int, float)) and strength < ACTIVE:
                stale.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "content": self._think_content(fact),
                        "strength": round(strength, 3),
                        "source": fact.get("source"),
                    }
                )
            if "contradiction_pending" in tags:
                contradicted.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "content": self._think_content(fact),
                        "tags": tags,
                    }
                )
            seen_ids.add(fact.get("fact_id"))

        unknown_count = 0 if merged else 1  # no results = unknown topic

        # Programmatic synthesis
        parts = []
        n_facts = len(merged)
        n_layers = len(recall_result.get("per_layer", {}))
        if n_facts:
            parts.append(
                f"Found {n_facts} result{'s' if n_facts != 1 else ''} across {n_layers} layer{'s' if n_layers != 1 else ''}."
            )
        else:
            parts.append(
                "No relevant facts found — this appears to be new/unexplored information."
            )
        if stale:
            parts.append(
                f"{len(stale)} fact{'s' if len(stale) != 1 else ''} {'are' if len(stale) != 1 else 'is'} stale (strength < {ACTIVE})."
            )
        if contradicted:
            parts.append(
                f"{len(contradicted)} fact{'s' if len(contradicted) != 1 else ''} {'are' if len(contradicted) != 1 else 'is'} flagged as contradicted."
            )
        if entities:
            parts.append(
                f"Reasoned across {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}: {', '.join(entities)}."
            )

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

    # ── verify — check verification-on-stop status ──

    def verify(
        self,
        status: str = "",
        command: str = "",
        output: str = "",
        spec_check: bool = False,
        changed_files: list[str] | None = None,
    ) -> dict:
        """Query or record verification-on-stop status.

        When called with no args, returns the current verification status from
        the ledger (including a nudge message if code edits need verification).

        When called with ``status`` set to ``"passed"``, ``"failed"``, or
        ``"skipped"``, records the verification event in the ledger.

        Pass ``changed_files`` to tell eling which files were edited (required
        for MCP-based agents like OpenCode that edit files outside eling).
        Edits are recorded in the ledger before checking status.

        Set ``spec_check=True`` to also run spec-kit conformance verification.
        Spec-kit results are returned in the ``"spec_kit"`` key and appended
        to the nudge message when requirements are uncovered.

        This is a **conditional** feature — it only activates when the host
        agent does NOT have built-in verify-on-stop (auto-detected from env
        or ``ELING_ADAPTER`` config). When running under Hermes, this is a
        no-op that returns ``{"host_has_verify": True}``.
        """
        from . import verify_on_stop as vos

        if vos.host_has_verify_on_stop(adapter=self._adapter):
            return {"host_has_verify": True, "active": False}

        # Record file edits from MCP agent (OpenCode, etc.) that edits files
        # outside eling — this is the primary way edits enter the ledger.
        if changed_files:
            for fp in changed_files:
                if fp and isinstance(fp, str):
                    vos.record_edit(fp)

        if status:
            vos.record_verification(status=status, command=command, output=output)
            return {
                "host_has_verify": False,
                "active": True,
                "recorded": True,
                "status": status,
            }

        result = vos.verify_status()

        # Spec-kit check (on demand or always when project path is set)
        if spec_check or result.get("needs_verification"):
            sk_result = vos._spec_kit_check()
            result["spec_kit"] = sk_result
            if sk_result.get("detected") and sk_result.get("nudge"):
                sk_nudge = sk_result["nudge"]
                existing = result.get("nudge") or ""
                if existing:
                    result["nudge"] = existing + "\n" + sk_nudge
                else:
                    result["nudge"] = sk_nudge

        return {
            "host_has_verify": False,
            "active": True,
            **result,
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

        Facts are auto-routed under the appropriate child page based on their
        category (project_summary → 🎯 Project Summaries, credential → 🔑
        Credentials, etc.). Uncategorised facts go to 📋 Task Logs.
        Pass explicit parent_page_id to bypass this routing.
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

        # Resolve effective parent: explicit > category child page > Task Logs > configured parent
        effective_parent = parent_page_id
        if not effective_parent:
            fact_cat = fact.get("category", "")
            notion_cat = _detect_notion_category(
                fact["content"], category_hint=fact_cat
            )
            effective_parent = (
                self._route_parent(notion_cat) or self.notion.parent_page_id
            )
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
        result = {
            "home": str(self.home),
            "facts": self.facts.stats(),
            "kb": self.kb.stats(),
            "code_available": self.code.available,
            "notion_available": self.notion.available,
            "builtin_available": self.builtin.available,
            "privacy": self.privacy.stats(),
            "hooks": {
                "total_handlers": self.hooks.total_handlers,
                "hooks_with_handlers": sum(
                    1 for h in eling_hooks.ALL_HOOKS if self.hooks.has_handlers(h)
                ),
            },
        }
        result["facts"]["versioning"] = self.facts.versioning_stats()
        return result

    # ── memory linking — Zettelkasten connections (v0.3.0) ──

    def link_stats(self) -> dict:
        """Statistics about the Zettelkasten fact link graph."""
        return self.facts.link_stats()

    def linked_facts(self, fact_id: int, limit: int = 10) -> list[dict]:
        """Return facts linked to *fact_id* by Zettelkasten linking."""
        return self.facts.linked_facts(fact_id, limit=limit)

    def evolve(self, threshold: float | None = None) -> dict:
        """Memory evolution: merge near-duplicate facts.

        Scans all facts for pairs with high Jaccard similarity and merges
        them — preserves content, averages trust, merges entities and links.
        """
        return self.facts.evolve(threshold=threshold)

    # ── Temporal Queries (Memvid-inspired) ────────────────────────────

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
    ) -> dict:
        """Cross-layer search with temporal filtering.

        When time_start is None but the query has temporal intent (e.g.
        "yesterday", "last week", "2026-07-01"), the time range is extracted
        automatically from the query.

        Supports same layers and RRF fusion as recall().
        """
        # Auto-detect temporal intent from query
        parsed_start = time_start
        parsed_end = time_end
        if not time_start and not time_end:
            if self.facts.has_temporal_intent(query):
                parsed_start, parsed_end = self.facts.parse_time_query(query)

        per_layer: dict[str, list[dict]] = {}

        # Use search_temporal on facts layer
        per_layer["facts"] = self.facts.search_temporal(
            query,
            time_start=parsed_start,
            time_end=parsed_end,
            category=category,
            source=source,
            min_trust=min_trust,
            limit=limit,
            include_cleared=include_cleared,
        )

        # Other layers (non-temporal) as-is
        if "kb" in (["kb"] if not category else []):
            per_layer["kb"] = self.kb.search(query, source=source, limit=limit)
        if "notion" in (["notion"] if not category else []) and self.notion.available:
            per_layer["notion"] = self.notion.search(query, limit=limit)

        merged = self._rrf_fuse(per_layer, limit=limit)
        return {
            "query": query,
            "temporal_range": {"start": parsed_start, "end": parsed_end},
            "merged": merged,
            "per_layer": per_layer,
        }

    # ── Per-Fact Versioning (Memvid-inspired) ─────────────────────────

    def versioned_update(
        self, fact_id: int, new_content: str, reason: str = ""
    ) -> dict | None:
        """Update a fact with version tracking."""
        return self.facts.versioned_update(fact_id, new_content, reason=reason)

    def get_version_history(self, fact_id: int, limit: int = 20) -> list[dict]:
        """Return all version records for a fact."""
        return self.facts.get_version_history(fact_id, limit=limit)

    def undo_to_version(self, fact_id: int, version_id: int) -> dict | None:
        """Rollback a fact to a previous version."""
        return self.facts.undo_to_version(fact_id, version_id)

    def versioning_stats(self) -> dict:
        """Return versioning statistics."""
        return self.facts.versioning_stats()

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
        self._fire_hook(
            "sync_start",
            {
                "direction": direction,
                "layer": layer,
            },
        )

        try:
            # ── flush: persist pending writes ──
            if direction in ("flush", "all", "auto"):
                self.facts.flush()
                self.kb.flush()
                result["layers"]["facts_flushed"] = True
                result["layers"]["kb_flushed"] = True

            # ── push: facts → Notion ──
            if direction in ("push", "all", "auto") and layer in (
                "auto",
                "facts",
                "notion",
            ):
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
                        logger.debug(
                            "sync state parse failed (non-fatal): %s", state_path
                        )
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
            self._fire_hook(
                "sync_complete",
                {
                    "direction": direction,
                    "layer": layer,
                    "result": result,
                },
            )

        except Exception as e:
            self._fire_hook(
                "sync_error",
                {
                    "direction": direction,
                    "layer": layer,
                    "error": str(e),
                },
            )
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
                logger.debug(
                    "sync push cache parse failed (non-fatal): %s", synced_path
                )

        for f in all_facts:
            f.get("id", f.get("fact_id"))
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
                if any(
                    "notion:" + page_id in str(r.get("source", "")) for r in existing
                ):
                    continue
                md = self.notion.get_page_markdown(page_id)
                if md:
                    source = f"notion:{page_id}"
                    meta = f"Title: {title}\nURL: {url}\n"
                    self.kb.index(source=source, content=meta + md[:4000])
                    pulled += 1
        except Exception:
            logger.debug("notion pull iteration failed (non-fatal)", exc_info=True)
        return pulled

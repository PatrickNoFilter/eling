"""Eling MCP Server — notion-only memory layer.

Writes and reads from Notion as the persistent second brain.
Local memory layers (facts, KB, code, builtin, HRR) are now served
by the `as_brain` MCP server (from `eling.as_brain.mcp_server`).

Protocol: MCP 2024-11-05, JSON-RPC over stdio.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from typing import Any

from .layers.notion import NotionLayer

logger = logging.getLogger(__name__)

_notion: NotionLayer | None = None


def _get_notion() -> NotionLayer:
    global _notion
    if _notion is None:
        _notion = NotionLayer(
            api_key=os.environ.get("NOTION_API_KEY"),
            parent_page_id=os.environ.get("NOTION_PARENT_PAGE_ID"),
        )
    return _notion


# ── Tool definitions (Notion only) ────────────────────────────────────────────

TOOLS = [
    {
        "name": "eling_remember",
        "description": "Store content as a Notion page. "
        "Requires NOTION_API_KEY and NOTION_PARENT_PAGE_ID to be set. "
        "Use category hint to auto-route to a child page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Content to store as a Notion page",
                },
                "title": {
                    "type": "string",
                    "default": "",
                    "description": "Page title (defaults to first 80 chars of content)",
                },
                "category": {
                    "type": "string",
                    "default": "general",
                    "description": "Category for auto-routing to child pages (credential, config, address, project_summary, general)",
                },
                "source": {
                    "type": "string",
                    "default": "mcp",
                    "description": "Agent identity tag",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "eling_search",
        "description": "Search Notion pages by title. "
        "Returns page id, title, URL, and last_edited time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for page title matching",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max results",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "eling_get_page",
        "description": "Fetch a Notion page's content as markdown. Uses the full-markdown endpoint by default, so secret values (API tokens, etc.) are returned un-truncated. Falls back to the block walk if the markdown endpoint is unavailable. For explicit control use eling_get_page_full.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "eling_get_page_full",
        "description": "Fetch a Notion page's content as markdown via the /v1/pages/<id>/markdown endpoint. Returns FULL, un-truncated content including complete API tokens (unlike eling_get_page which truncates secrets). Use this for credential pages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "eling_create_page",
        "description": "Create a new Notion page under the configured parent. "
        "Returns the new page_id on success.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Page title",
                },
                "content": {
                    "type": "string",
                    "description": "Page body in markdown",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "eling_stats",
        "description": "Check Notion connection status and configuration.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "eling_delete_page",
        "description": "Delete (archive) a Notion page by id. "
        "Notion removes pages by archiving; pass hard=true to also purge the "
        "page's block children first so a restored page is empty. "
        "Requires NOTION_API_KEY to be set.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID to delete/archive",
                },
                "hard": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, purge block children before archiving",
                },
            },
            "required": ["page_id"],
        },
    },
]


# ── MCP protocol handler ──────────────────────────────────────────────────────


def _handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params", {})

    try:
        if method == "initialize":
            return _handle_initialize(rid, params)
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            return _handle_tool_call(rid, params)
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        else:
            return _error(rid, -32601, f"unknown method: {method}")
    except Exception as e:
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _handle_initialize(rid: int | str | None, params: dict) -> dict:
    client_info = params.get("clientInfo", {})
    client_name = client_info.get("name", "unknown")
    client_version = client_info.get("version", "?")
    logger.info(
        "Eling (Notion) MCP client connected: %s %s", client_name, client_version
    )
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eling-notion", "version": "1.0.0"},
        },
    }


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    notion = _get_notion()

    def ok(data: Any) -> dict:
        try:
            text = json.dumps(data, default=str)
        except Exception:
            text = json.dumps(
                {"error": "result not serializable", "raw": str(data)[:500]}
            )
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    try:
        if tool_name == "eling_remember":
            content = args.pop("content", "")
            title = args.pop("title", "") or content[:80]
            category = args.pop("category", "general")
            # Simple category-based child page routing
            notion_cat = (
                category
                if category in ("credential", "config", "address", "project_summary")
                else "task_logs"
            )
            if not notion.available:
                return ok(
                    {
                        "error": "Notion not configured (NOTION_API_KEY missing)",
                        "available": False,
                    }
                )
            page_id = notion.create_page(title=title, content=content)
            return ok({"layer": "notion", "page_id": page_id, "category": notion_cat})
        elif tool_name == "eling_search":
            query = args.pop("query", "")
            limit = args.pop("limit", 10)
            if not notion.available:
                return ok({"error": "Notion not configured", "available": False})
            results = notion.search(query, limit=limit)
            return ok({"query": query, "results": results})
        elif tool_name == "eling_get_page":
            page_id = args.pop("page_id", "")
            if not notion.available:
                return ok({"error": "Notion not configured", "available": False})
            md = notion.get_page_markdown(page_id, prefer_full=True)
            return ok({"page_id": page_id, "markdown": md, "truncated": False})
        elif tool_name == "eling_get_page_full":
            page_id = args.pop("page_id", "")
            if not notion.available:
                return ok({"error": "Notion not configured", "available": False})
            md = notion.get_page_full_markdown(page_id)
            return ok({"page_id": page_id, "markdown": md, "truncated": False})
        elif tool_name == "eling_create_page":
            title = args.pop("title", "")
            content = args.pop("content", "")
            if not notion.available:
                return ok({"error": "Notion not configured", "available": False})
            page_id = notion.create_page(title=title, content=content)
            return ok({"page_id": page_id, "title": title})
        elif tool_name == "eling_stats":
            return ok(
                {
                    "available": notion.available,
                    "has_api_key": bool(notion.api_key),
                    "has_parent_page_id": bool(notion.parent_page_id),
                }
            )
        elif tool_name == "eling_delete_page":
            page_id = args.pop("page_id", "")
            hard = bool(args.pop("hard", False))
            if not notion.available:
                return ok({"error": "Notion not configured", "available": False})
            if not page_id:
                return ok({"error": "page_id is required", "success": False})
            success = notion.delete_page(page_id, hard=hard)
            return ok({"page_id": page_id, "success": success, "hard": hard})
        else:
            return _error(rid, -32601, f"unknown tool: {tool_name}")
    except Exception as e:
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _error(
    rid: int | str | None, code: int, message: str, data: str | None = None
) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


# ── Stdio entry point ─────────────────────────────────────────────────────────


def run_stdio() -> None:
    """Run MCP server over stdio (one JSON-RPC per line)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(req)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run_stdio()

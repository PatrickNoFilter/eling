"""Eling MCP Server — shared brain for all AI agents.

Protocol: MCP 2024-11-05, JSON-RPC over stdio.
Compatible with: Hermes, OpenCode, OpenCLAW, OpenClaude, Claude Code, Cursor, Windsurf.

Every tool accepts an optional `source` param to tag/scope by agent origin.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from typing import Any

from .brain import Brain

logger = logging.getLogger(__name__)

_brain: Brain | None = None


def _get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "eling_remember",
        "description": "Store content in memory. "
        "Auto-routes: short (<500 chars) → facts layer, long/markdown → KB. "
        "Use source='agent_name' to tag which agent stored it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Content to remember",
                },
                "layer": {
                    "type": "string",
                    "enum": ["auto", "facts", "kb", "notion"],
                    "default": "auto",
                    "description": "Target layer: auto (default), facts, kb, notion",
                },
                "category": {
                    "type": "string",
                    "default": "general",
                    "description": "Category for facts layer (e.g. config, testing, deploy)",
                },
                "tags": {
                    "type": "string",
                    "default": "",
                    "description": "Comma-separated tags for facts layer",
                },
                "source": {
                    "type": "string",
                    "default": "mcp",
                    "description": "Agent identity — who stored this (hermes, opencode, etc.)",
                },
                "title": {
                    "type": "string",
                    "default": "",
                    "description": "Page title for notion layer",
                },
                "skip_dedup": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip SHA-256 dedup check",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "eling_recall",
        "description": "Search across all memory layers with RRF fusion. "
        "Set source='agent_name' to scope to one agent's memories only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (BM25 + Jaccard + optional HRR)",
                },
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Layers: builtin, facts, kb, code, notion (default: all)",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max merged results",
                },
                "source": {
                    "type": "string",
                    "default": "",
                    "description": "Filter by agent source (hermes, opencode, etc.). Empty = all agents.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "eling_reason",
        "description": "Find facts connecting MULTIPLE entities via compositional HRR queries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entities to connect (e.g. pytest, HRR)",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                },
            },
            "required": ["entities"],
        },
    },
    {
        "name": "eling_probe",
        "description": "Get all facts about a single entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity name to probe",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                },
            },
            "required": ["entity"],
        },
    },
    {
        "name": "eling_reflect",
        "description": "Promote a high-trust fact to Notion as a permanent page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "integer",
                    "description": "Fact ID to promote",
                },
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "eling_sync",
        "description": "Synchronize data between memory layers (facts → Notion, flush to disk).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["push", "pull", "flush", "all"],
                    "default": "all",
                    "description": "push=facts→Notion, pull=Notion→KB, flush=disk, all=both",
                },
                "layer": {
                    "type": "string",
                    "enum": ["auto", "facts", "notion", "kb"],
                    "default": "auto",
                },
            },
        },
    },
    {
        "name": "eling_stats",
        "description": "Get statistics about all memory layers.",
        "inputSchema": {"type": "object", "properties": {}},
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
    logger.info("MCP client connected: %s %s", client_name, client_version)
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eling", "version": "0.2.0"},
        },
    }


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    brain = _get_brain()

    def ok(data: Any) -> dict:
        text = json.dumps(data, default=str)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    try:
        if tool_name == "eling_remember":
            return ok(brain.remember(**args))
        elif tool_name == "eling_recall":
            return ok(brain.recall(**args))
        elif tool_name == "eling_reason":
            return ok(brain.reason(**args))
        elif tool_name == "eling_probe":
            entity = args.pop("entity", "")
            limit = args.pop("limit", 10)
            return ok(brain.probe(entity, limit=limit))
        elif tool_name == "eling_reflect":
            return ok(brain.reflect(**args))
        elif tool_name == "eling_sync":
            return ok(brain.sync(**args))
        elif tool_name == "eling_stats":
            return ok(brain.stats())
        else:
            return _error(rid, -32601, f"unknown tool: {tool_name}")
    except Exception as e:
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _error(rid: int | str | None, code: int, message: str, data: str | None = None) -> dict:
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

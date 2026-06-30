"""Eling MCP server — exposes 5 tools over stdio JSON-RPC."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from .brain import Brain

_brain: Brain | None = None


def _get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain


TOOLS = [
    {
        "name": "eling_remember",
        "description": "Store content in the appropriate memory layer (facts/kb/notion). Auto-routes based on length.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "layer": {"type": "string", "enum": ["auto", "facts", "kb", "notion"], "default": "auto"},
                "category": {"type": "string", "default": "general"},
                "tags": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
            },
            "required": ["content"],
        },
    },
    {
        "name": "eling_recall",
        "description": "Cross-layer semantic search with RRF fusion. Returns merged + per-layer results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "layers": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "eling_reason",
        "description": "Find facts connecting MULTIPLE entities (compositional HRR query).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["entities"],
        },
    },
    {
        "name": "eling_reflect",
        "description": "Promote a high-trust fact to Notion as a new page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer"},
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "eling_stats",
        "description": "Get statistics about all memory layers.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle(req: dict) -> dict:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {})

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "eling", "version": "0.1.0"},
                },
            }
        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            brain = _get_brain()

            if tool_name == "eling_remember":
                result = brain.remember(**args)
            elif tool_name == "eling_recall":
                result = brain.recall(**args)
            elif tool_name == "eling_reason":
                result = brain.reason(**args)
            elif tool_name == "eling_reflect":
                result = brain.reflect(**args)
            elif tool_name == "eling_stats":
                result = brain.stats()
            else:
                return {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32601, "message": f"unknown tool: {tool_name}"}}

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, default=str)}]},
            }
        elif method == "notifications/initialized":
            return None  # no response for notifications
        else:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"}}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": f"{type(e).__name__}: {e}", "data": traceback.format_exc()}}


def run_stdio():
    """Run MCP server over stdio."""
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
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    run_stdio()

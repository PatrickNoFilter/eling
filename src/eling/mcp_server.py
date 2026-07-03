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


def _resolve_adapter(client_name: str) -> str:
    """Map MCP client name to eling adapter string.

    Uses the actual MCP client that connected (from initialize handshake),
    which is more reliable than environment variable heuristics.
    """
    name = (client_name or "").lower().strip()
    if "opencode" in name:
        return "opencode"
    if "openclaw" in name:
        return "openclaw"
    if "openclaude" in name:
        return "openclaude"
    if "hermes" in name:
        return "hermes"
    if "cursor" in name:
        return "cursor"
    if "windsurf" in name:
        return "windsurf"
    # Claude Code (Anthropic's official MCP client)
    if "claude code" in name or name in ("claude",):
        return "claude_cli"
    return "auto"


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
    {
        "name": "eling_think",
        "description": "Synthesis + gap-analysis. Runs recall + reason, returns results plus stale/contradicted/unknown analysis. Keeps the cheap eling_recall path unchanged.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional entities to reason across (compositional HRR query)",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max results to analyze",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "eling_export",
        "description": "Export all memory layers as JSON or Markdown. Portable snapshot for migration, backup, or debug inspection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["json", "markdown"],
                    "default": "json",
                    "description": "Output format",
                },
                "path": {
                    "type": "string",
                    "default": "",
                    "description": "Optional file path to write to (default: no file, returns preview only)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "eling_verify",
        "description": "Check or record verification-on-stop status. "
        "When host agent (Hermes) already has verify-on-stop, returns "
        "{host_has_verify: true, active: false}. "
        "When host agent lacks verification (OpenCode, etc.), returns "
        "current status or records a verification event. "
        "Call with no args to query status; pass status='passed'/'failed'/'skipped' "
        "with optional command and output to record a verification event.\n\n"
        "IMPORTANT: If you edited files before calling this, pass them in "
        "changed_files so eling can track what needs verification. "
        "Without changed_files, eling has no knowledge of file edits "
        "from MCP agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["", "passed", "failed", "skipped"],
                    "default": "",
                    "description": "Verification result. Empty = query mode. Set to 'passed'/'failed'/'skipped' to record.",
                },
                "command": {
                    "type": "string",
                    "default": "",
                    "description": "The command that was run (e.g. 'pytest')",
                },
                "output": {
                    "type": "string",
                    "default": "",
                    "description": "Command output (truncated to 500 chars)",
                },
                "changed_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files edited in this turn. Pass these so eling knows verification is needed.",
                },
                "spec_check": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also run spec-kit conformance verification",
                },
            },
            "required": [],
        },
    },
    {
        "name": "eling_verify_spec",
        "description": "Run spec-kit conformance verification. "
        "Detects spec-kit artifacts (specs/<feature>/spec.md, plan.md, tasks.md) "
        "and checks whether the current code implementation covers each "
        "spec requirement. Returns coverage stats, uncovered requirements, "
        "and a nudge message. "
        "Use this to verify that code changes match the project specification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "changed_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of changed file paths to check coverage against",
                },
            },
            "required": [],
        },
    },
    {
        "name": "eling_link_stats",
        "description": "Statistics about the Zettelkasten fact link graph: "
        "total links, linked fact count, average links per fact.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "eling_linked_facts",
        "description": "Return facts linked to a given fact_id, ordered by link weight. "
        "Uses Zettelkasten-style automatic linking (A-MEM).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "integer",
                    "description": "Fact ID to get linked facts for",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max links to return",
                },
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "eling_evolve",
        "description": "Trigger a memory evolution pass: scan all facts for near-duplicate "
        "pairs (Jaccard similarity >= threshold) and merge them — combines content, "
        "averages trust, merges entities and fact links. Returns merge count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "default": 0.65,
                    "description": "Jaccard similarity threshold for merge (default 0.65)",
                },
            },
            "required": [],
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
    logger.info("MCP client connected: %s %s", client_name, client_version)
    # Detect adapter from the actual MCP client that connected
    brain = _get_brain()
    detected = _resolve_adapter(client_name)
    brain._adapter = detected
    logger.info("eling adapter set to %r (from client name %r)", detected, client_name)
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eling", "version": "0.4.0"},
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
        elif tool_name == "eling_think":
            return ok(brain.think(**args))
        elif tool_name == "eling_export":
            fmt = args.pop("format", "json")
            path = args.pop("path", None) or None
            return ok(brain.export(format=fmt, path=path))
        elif tool_name == "eling_verify":
            status = args.pop("status", "")
            command = args.pop("command", "")
            output = args.pop("output", "")
            spec_check = args.pop("spec_check", False)
            changed_files = args.pop("changed_files", None)
            return ok(brain.verify(status=status, command=command, output=output,
                                   spec_check=spec_check, changed_files=changed_files))
        elif tool_name == "eling_verify_spec":
            changed_files = args.pop("changed_files", None)
            from .spec_kit import SpecKitVerifier
            project_path = getattr(brain, "_project_path", None)
            v = SpecKitVerifier(project_path) if project_path else SpecKitVerifier()
            result = v.verify(changed_files=changed_files)
            return ok(result)
        elif tool_name == "eling_link_stats":
            return ok(brain.link_stats())
        elif tool_name == "eling_linked_facts":
            return ok(brain.linked_facts(**args))
        elif tool_name == "eling_evolve":
            threshold = args.pop("threshold", None)
            return ok(brain.evolve(threshold=threshold))
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

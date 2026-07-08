"""As Brain MCP Server — local memory layers for AI agents.

Exposes: facts / KB / code / builtin / HRR layers.
Notion layer is intentionally excluded (handled by the `eling` MCP server).

Protocol: MCP 2024-11-05, JSON-RPC over stdio.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from eling.brain import Brain

logger = logging.getLogger(__name__)

_brain: Brain | None = None
_brain_init_error: str | None = None

# Agent identity captured from the MCP `initialize` handshake (clientInfo.name).
# Gap #1: every brain_* tool that accepts `source` falls back to this so the
# host agent is auto-attributed as the memory owner without manual tagging.
_handshake_source: str = "mcp"


def _resolve_home() -> str | None:
    """Gap #2: ELING_HOME override is first-class and explicit.

    Returns the resolved ELING_HOME path when set, else None so Brain() falls
    back to its own default resolution ($HERMES_HOME/eling or ~/.eling).
    """
    env = os.environ.get("ELING_HOME")
    if env:
        return str(Path(env).expanduser())
    return None


def _get_brain() -> Brain:
    global _brain, _brain_init_error
    if _brain is None:
        try:
            # Gap #2: resolve ELING_HOME explicitly so the override is honoured
            # even when Brain()'s internal fallback chain changes.
            home = _resolve_home()
            _brain = Brain(home=home) if home else Brain()
            _brain_init_error = None
        except Exception as exc:
            _brain_init_error = f"{type(exc).__name__}: {exc}"
            logger.error("AsBrain init failed: %s", _brain_init_error, exc_info=True)
            raise
    return _brain


# ── Tool definitions (all layers EXCEPT Notion) ──────────────────────────────

TOOLS = [
    {
        "name": "brain_remember",
        "description": "Store content in local memory. "
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
                    "enum": ["auto", "facts", "kb"],
                    "default": "auto",
                    "description": "Target layer: auto (default), facts, kb",
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
                    "description": "Section/title for KB layer",
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
        "name": "brain_recall",
        "description": "Search across all local memory layers with RRF fusion. "
        "Does NOT search Notion (use mcp.eling for that). "
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
                    "description": "Layers: builtin, facts, kb, code (default: all)",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max merged results",
                },
                "source": {
                    "type": "string",
                    "default": "",
                    "description": "Filter by agent source. Empty = all agents.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_reason",
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
        "name": "brain_probe",
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
        "name": "brain_think",
        "description": "Synthesis + gap-analysis. Runs recall + reason, returns results plus stale/contradicted/unknown analysis.",
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
        "name": "brain_stats",
        "description": "Get statistics about all local memory layers (facts, KB, code, builtin).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_export",
        "description": "Export local memory layers as JSON or Markdown. Portable snapshot for migration, backup, or debug inspection.",
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
                    "description": "Optional file path to write to (default: returns preview only)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "brain_link_stats",
        "description": "Statistics about the Zettelkasten fact link graph: total links, linked fact count, average links per fact.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_linked_facts",
        "description": "Return facts linked to a given fact_id, ordered by link weight. Uses Zettelkasten-style automatic linking.",
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
        "name": "brain_evolve",
        "description": "Trigger a memory evolution pass: scan all facts for near-duplicate pairs and merge them.",
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
    {
        "name": "brain_snapshot",
        "description": "Create a named snapshot of the facts database. Use this before destructive operations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "default": "",
                    "description": "Why the snapshot is taken (e.g. 'pre_evolution')",
                },
            },
            "required": [],
        },
    },
    {
        "name": "brain_list_snapshots",
        "description": "List all available snapshots, newest first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_rollback",
        "description": "Rollback the facts database to a named snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "snapshot_id": {
                    "type": "string",
                    "description": "Snapshot ID to restore (from brain_list_snapshots)",
                },
            },
            "required": ["snapshot_id"],
        },
    },
    {
        "name": "brain_search_temporal",
        "description": "Cross-layer search with temporal filtering across local memory layers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (may include temporal keywords)"},
                "time_start": {"type": "string", "description": "ISO start date filter (optional)"},
                "time_end": {"type": "string", "description": "ISO end date filter (optional)"},
                "category": {"type": "string", "description": "Fact category filter"},
                "source": {"type": "string", "description": "Source filter"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_versioned_update",
        "description": "Update a fact with full version tracking (append-only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "Fact ID to update"},
                "new_content": {"type": "string", "description": "New content for the fact"},
                "reason": {"type": "string", "description": "Reason for the update"},
            },
            "required": ["fact_id", "new_content"],
        },
    },
    {
        "name": "brain_get_version_history",
        "description": "Return all version records for a fact, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "Fact ID"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "brain_undo_to_version",
        "description": "Rollback a fact to a previous version.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "Fact ID"},
                "version_id": {"type": "integer", "description": "Version ID to restore"},
            },
            "required": ["fact_id", "version_id"],
        },
    },
    {
        "name": "brain_versioning_stats",
        "description": "Return versioning statistics (total versions, versioned facts, etc.).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_verify",
        "description": "Check or record verification-on-stop status for local memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["", "passed", "failed", "skipped"],
                    "default": "",
                    "description": "Verification result. Empty = query mode.",
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
                    "description": "Files edited in this turn.",
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
        "name": "brain_verify_spec",
        "description": "Run spec-kit conformance verification on local codebase.",
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
    global _handshake_source
    client_info = params.get("clientInfo", {})
    client_name = client_info.get("name", "unknown")
    client_version = client_info.get("version", "?")
    # Gap #1: capture the host agent identity from the MCP handshake so
    # subsequent brain_* calls auto-attribute memory to the right agent.
    if client_name and client_name != "unknown":
        _handshake_source = client_name
    logger.info("AsBrain MCP client connected: %s %s", client_name, client_version)
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "as-brain", "version": "1.0.0"},
        },
    }


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    brain = _get_brain()

    def ok(data: Any) -> dict:
        try:
            text = json.dumps(data, default=str)
        except Exception:
            text = json.dumps({"error": "result not serializable", "raw": str(data)[:500]})
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    try:
        if tool_name == "brain_remember":
            # Gap #1: auto-source from the MCP handshake when caller omits source
            args.setdefault("source", _handshake_source)
            return ok(brain.remember(**args))
        elif tool_name == "brain_recall":
            # Filter out 'notion' from layers by default
            layers = args.pop("layers", None)
            if layers is None:
                layers = ["builtin", "facts", "kb", "code"]
            return ok(brain.recall(layers=layers, **args))
        elif tool_name == "brain_reason":
            return ok(brain.reason(**args))
        elif tool_name == "brain_probe":
            entity = args.pop("entity", "")
            limit = args.pop("limit", 10)
            return ok(brain.probe(entity, limit=limit))
        elif tool_name == "brain_think":
            return ok(brain.think(**args))
        elif tool_name == "brain_stats":
            return ok(brain.stats())
        elif tool_name == "brain_export":
            fmt = args.pop("format", "json")
            path = args.pop("path", None) or None
            return ok(brain.export(format=fmt, path=path))
        elif tool_name == "brain_link_stats":
            return ok(brain.link_stats())
        elif tool_name == "brain_linked_facts":
            return ok(brain.linked_facts(**args))
        elif tool_name == "brain_evolve":
            threshold = args.pop("threshold", None)
            return ok(brain.evolve(threshold=threshold))
        elif tool_name == "brain_snapshot":
            reason = args.pop("reason", "")
            return ok(brain.snapshot(reason=reason))
        elif tool_name == "brain_list_snapshots":
            return ok({"snapshots": brain.list_snapshots()})
        elif tool_name == "brain_rollback":
            snapshot_id = args.pop("snapshot_id", "")
            if not snapshot_id:
                return _error(rid, -32000, "snapshot_id is required")
            return ok(brain.rollback(snapshot_id))
        elif tool_name == "brain_search_temporal":
            query = args.pop("query", "")
            if not query:
                return _error(rid, -32000, "query is required")
            return ok(brain.search_temporal(query=query, **args))
        elif tool_name == "brain_versioned_update":
            return ok(brain.versioned_update(**args))
        elif tool_name == "brain_get_version_history":
            return ok(brain.get_version_history(**args))
        elif tool_name == "brain_undo_to_version":
            return ok(brain.undo_to_version(**args))
        elif tool_name == "brain_versioning_stats":
            return ok(brain.versioning_stats())
        elif tool_name == "brain_verify":
            status = args.pop("status", "")
            command = args.pop("command", "")
            output = args.pop("output", "")
            spec_check = args.pop("spec_check", False)
            changed_files = args.pop("changed_files", None)
            return ok(brain.verify(status=status, command=command, output=output,
                                   spec_check=spec_check, changed_files=changed_files))
        elif tool_name == "brain_verify_spec":
            changed_files = args.pop("changed_files", None)
            from eling.spec_kit import SpecKitVerifier
            project_path = getattr(brain, "_project_path", None)
            v = SpecKitVerifier(project_path) if project_path else SpecKitVerifier()
            result = v.verify(changed_files=changed_files)
            return ok(result)
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

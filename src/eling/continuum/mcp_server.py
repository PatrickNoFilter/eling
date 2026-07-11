"""Continuum Layer 6 — unified orchestration MCP server.

ONE MCP hub that every AI agent connects to. Point Claude Code, Codex, Cline,
Hermes, Zero, OpenCode, Cursor — all of them — at this server and they share
one memory + orchestration system, exactly like Continuum, but natively on
Python/Termux/PRoot (no Node, no NestJS, no Astro).

Exposes continuum_* tools:
  - continuum_project_create / project_get / project_list
  - continuum_knowledge_create / knowledge_get / knowledge_list / knowledge_search
  - continuum_agent_register / agent_update / agent_get / registry_list
  - continuum_plot_get / plot_update
  - continuum_dispatch  (register agent + create isolated git worktree + return prompt)
  - continuum_reservations (collision check across active agents)

Protocol: MCP 2024-11-05, JSON-RPC over stdio. The host agent is auto-attributed
from the MCP ``initialize`` handshake (clientInfo.name), so memory/agents created
by each client are tagged with which agent they came from.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from . import worktree as wt
from .plot import apply_unified_diff, seed_plot
from .store import ContinuumStore

logger = logging.getLogger(__name__)

_store: ContinuumStore | None = None
_store_init_error: str | None = None
_handshake_source: str = "mcp"


def _resolve_db() -> str | None:
    """ELING_CONTINUUM_DB > ELING_HOME > HERMES_HOME-aware db path."""
    explicit = os.environ.get("ELING_CONTINUUM_DB")
    if explicit:
        return str(Path(explicit).expanduser())
    env = os.environ.get("ELING_HOME")
    if env:
        return str(Path(env).expanduser() / "continuum.db")
    hermes = os.environ.get("HERMES_HOME")
    if hermes:
        return str(Path(hermes).expanduser() / "eling" / "continuum.db")
    return None


def _get_store() -> ContinuumStore:
    global _store, _store_init_error
    if _store is None:
        try:
            db = _resolve_db()
            _store = ContinuumStore(db_path=db) if db else ContinuumStore()
            _store_init_error = None
        except Exception as exc:
            _store_init_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Continuum store init failed: %s", _store_init_error, exc_info=True
            )
            raise
    return _store


# ── Tool definitions ────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "continuum_project_create",
        "description": "Register a canonical project root so agents can share memory + orchestration on it. "
        "Pass the project root (never a worktree subdir).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Canonical absolute project root",
                },
                "name": {
                    "type": "string",
                    "default": "",
                    "description": "Optional human name",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "continuum_project_get",
        "description": "Get a registered project by path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "continuum_project_list",
        "description": "List all registered projects.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "continuum_knowledge_create",
        "description": "Store a lesson as a 768/1024-dim-vector (when embedder available) knowledge entry. "
        "kind='fundamental' = binding rule loaded every dispatch; kind='situational' = semantic search. "
        "Whole-content replace (re-embedded on save).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "slug": {"type": "string", "description": "Stable id for the lesson"},
                "content": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["fundamental", "situational"],
                    "default": "situational",
                },
                "title": {"type": "string", "default": ""},
                "embed": {
                    "type": "boolean",
                    "default": True,
                    "description": "Embed for semantic search if an embedder is available",
                },
            },
            "required": ["project", "slug", "content"],
        },
    },
    {
        "name": "continuum_knowledge_get",
        "description": "Fetch the full body of a knowledge entry by (project, slug).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "slug": {"type": "string"},
            },
            "required": ["project", "slug"],
        },
    },
    {
        "name": "continuum_knowledge_list",
        "description": "List metadata (slug, kind, agentSlug, timestamps) for knowledge entries. "
        "Pass kind='fundamental' to load binding rules; omit kind for all. Metadata-only — call "
        "knowledge_get for bodies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["fundamental", "situational", ""],
                    "default": "",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "continuum_knowledge_search",
        "description": "Semantic/BM25 ranked search over knowledge. Returns metadata only (cheap top-K). "
        "Call knowledge_get for the body of relevant hits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "q": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["project", "q"],
        },
    },
    {
        "name": "continuum_agent_register",
        "description": "Register a dispatch agent in the registry (state=draft). Pass reserved_paths (JSON globs) "
        "to reserve files; collisions with other active agents are reported.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "slug": {"type": "string"},
                "branch": {"type": "string", "default": ""},
                "worktree": {"type": "string", "default": ""},
                "prompt": {"type": "string", "default": ""},
                "reserved_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["project", "slug"],
        },
    },
    {
        "name": "continuum_agent_update",
        "description": "Update an agent: status transitions draft→active→merged|abandoned, branch, worktree, "
        "prompt, merged_commit (7-40 char SHA required for merged), reserved_paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "slug": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "merged", "abandoned"],
                },
                "branch": {"type": "string"},
                "worktree": {"type": "string"},
                "prompt": {"type": "string"},
                "merged_commit": {"type": "string"},
                "reserved_paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["project", "slug"],
        },
    },
    {
        "name": "continuum_agent_get",
        "description": "Get a single agent's full registry record.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}, "slug": {"type": "string"}},
            "required": ["project", "slug"],
        },
    },
    {
        "name": "continuum_registry_list",
        "description": "Paginate dispatch metadata across agents (slug, status, branch, reserved_count). "
        "Filter by status. The 'what did the other agent do?' view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "merged", "abandoned", ""],
                    "default": "",
                },
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["project"],
        },
    },
    {
        "name": "continuum_plot_get",
        "description": "Get the canonical PLOT.md protocol for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    },
    {
        "name": "continuum_plot_update",
        "description": "Mutate the PLOT.md via a unified diff (no full-text writes). On stale context, returns "
        "hunk_mismatch — re-read, regenerate the diff, retry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "diff": {"type": "string", "description": "Unified-diff text to apply"},
            },
            "required": ["project", "diff"],
        },
    },
    {
        "name": "continuum_dispatch",
        "description": "ONE-CALL DISPATCH: register agent (state=draft), create an isolated git worktree on a "
        "branch, and return a ready-to-paste prompt for a fresh agent. Auto checks reserved_path collisions. "
        "If the project is not a git repo, it still registers the agent (no worktree).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "slug": {"type": "string"},
                "goal": {
                    "type": "string",
                    "description": "What the fresh agent should do",
                },
                "reserved_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "base_branch": {
                    "type": "string",
                    "default": "",
                    "description": "Branch to fork the worktree from",
                },
            },
            "required": ["project", "slug", "goal"],
        },
    },
    {
        "name": "continuum_reservations",
        "description": "Show reserved_path collisions across ACTIVE agents on a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    },
]


# ── protocol handler ────────────────────────────────────────────────────────


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
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            return _handle_tool_call(rid, params)
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        else:
            return _error(rid, -32601, f"unknown method: {method}")
    except Exception as e:  # noqa: BLE001
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _handle_initialize(rid: int | str | None, params: dict) -> dict:
    global _handshake_source
    client = params.get("clientInfo", {})
    name = client.get("name", "unknown")
    if name and name != "unknown":
        _handshake_source = name
    logger.info(
        "Continuum MCP client connected: %s %s", name, client.get("version", "?")
    )
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eling-continuum", "version": "0.1.0"},
        },
    }


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    store = _get_store()
    agent = args.pop("agent_slug", None) or _handshake_source

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
        if tool_name == "continuum_project_create":
            name = args.pop("name", "") or None
            return ok(store.project_create(args["path"], name=name))
        elif tool_name == "continuum_project_get":
            return ok(store.project_get(args["path"]))
        elif tool_name == "continuum_project_list":
            return ok({"projects": store.project_list()})

        elif tool_name == "continuum_knowledge_create":
            return ok(store.knowledge_create(agent_slug=agent, **args))
        elif tool_name == "continuum_knowledge_get":
            return ok(store.knowledge_get(args["project"], args["slug"]))
        elif tool_name == "continuum_knowledge_list":
            kind = args.get("kind") or None
            return ok({"knowledge": store.knowledge_list(args["project"], kind=kind)})
        elif tool_name == "continuum_knowledge_search":
            return ok(
                {
                    "results": store.knowledge_search(
                        args["project"], args["q"], args.get("limit", 10)
                    )
                }
            )

        elif tool_name == "continuum_agent_register":
            return ok(store.agent_register(agent_slug=agent, **args))
        elif tool_name == "continuum_agent_update":
            return ok(store.agent_update(**args))
        elif tool_name == "continuum_agent_get":
            return ok(store.agent_get(args["project"], args["slug"]))
        elif tool_name == "continuum_registry_list":
            status = args.get("status") or None
            return ok(
                {
                    "agents": store.registry_list(
                        args["project"],
                        status=status,
                        limit=args.get("limit", 50),
                        offset=args.get("offset", 0),
                    )
                }
            )

        elif tool_name == "continuum_plot_get":
            content = store.plot_get(args["project"])
            if content is None:
                content = seed_plot(Path(args["project"]).name)
                store.plot_set(args["project"], content)
            return ok({"content": content})
        elif tool_name == "continuum_plot_update":
            current = store.plot_get(args["project"])
            if current is None:
                current = seed_plot(Path(args["project"]).name)
            new = apply_unified_diff(current, args["diff"])
            return ok(store.plot_set(args["project"], new))

        elif tool_name == "continuum_reservations":
            agents = store.registry_list(args["project"])
            collisions = []
            for a in agents:
                if a["status"] != "active":
                    continue
                rp = a.get("reserved_paths") or []
                if isinstance(rp, str):
                    import json as _json

                    rp = _json.loads(rp or "[]")
                collisions.append({"agent": a["slug"], "reserved_paths": rp})
            return ok({"active_reservations": collisions})

        elif tool_name == "continuum_dispatch":
            project = args["project"]
            slug = args["slug"]
            goal = args["goal"]
            reserved = args.get("reserved_paths", [])
            # 1. register (collision-checked)
            reg = store.agent_register(
                project=project,
                slug=slug,
                agent_slug=agent,
                branch=slug,
                reserved_paths=reserved,
            )
            # 2. try isolated git worktree
            worktree_path = ""
            worktree_note = ""
            if wt.is_git_repo(project):
                try:
                    worktree_path = wt.dispatch_worktree(
                        project, branch=slug, base=args.get("base_branch") or None
                    )
                except Exception as exc:  # noqa: BLE001
                    worktree_note = f"worktree creation skipped: {exc}"
            else:
                worktree_note = "not a git repo — agent registered without a worktree"
            # 3. ready-to-paste prompt (the orchestrator hands this to a fresh agent)
            prompt = (
                f"# Dispatch: {slug}\n\n"
                f"Goal: {goal}\n\n"
                f"Work in: {worktree_path or project}\n"
                f"Branch: {slug}\n\n"
                f"Before you start:\n"
                f"1. Read PLOT.md for this project.\n"
                f"2. knowledge_list(kind='fundamental') then knowledge_get each.\n"
                f"3. knowledge_search(q='{goal}') for situational lessons.\n\n"
                f"When done:\n"
                f"- knowledge_create(kind='situational', ...) for what you learned.\n"
                f"- agent_update(status='merged', merged_commit='<sha>')\n"
            )
            store.agent_update(
                project=project, slug=slug, prompt=prompt, worktree=worktree_path
            )
            reg["worktree"] = worktree_path
            reg["prompt"] = prompt
            if worktree_note:
                reg["note"] = worktree_note
            return ok(reg)

        else:
            return _error(rid, -32601, f"unknown tool: {tool_name}")
    except Exception as e:  # noqa: BLE001
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _error(
    rid: int | str | None, code: int, message: str, data: str | None = None
) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


# ── stdio entry ─────────────────────────────────────────────────────────────


def run_stdio() -> None:
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

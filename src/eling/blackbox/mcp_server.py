"""Blackbox MCP Server — Layer 2 flight recorder telemetry.

Exposes 15+ blackbox_* tools for querying telemetry, scoring runs,
starting/stopping agent watches, managing baselines, and generating
handoff summaries.

Protocol: MCP 2024-11-05, JSON-RPC over stdio.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from .core import (
    TraceEvent,
    EventType,
    RunSummary,
    TaskArchetype,
    EfficiencyReport,
)
from .store import BlackboxStore
from .score import EfficiencyScorer
from .effectiveness import EffectivenessScorer
from .timeline import CausalTimeline
from .adapters.zero import (
    ZeroAdapter,
    install_zero_plugin,
    build_summary_from_zero_events,
)
from .adapters.hermes import HermesAdapter

logger = logging.getLogger(__name__)

_store: BlackboxStore | None = None
_zero_adapter: ZeroAdapter | None = None
_hermes_adapter: HermesAdapter | None = None


def _get_store() -> BlackboxStore:
    global _store
    if _store is None:
        db_path = os.environ.get("ELING_BLACKBOX_DB")
        _store = BlackboxStore(db_path=db_path) if db_path else BlackboxStore()
    return _store


TOOLS = [
    {
        "name": "blackbox_watch_start",
        "description": "Start watching an agent's telemetry stream. "
        "Supported hosts: 'zero' (stream-JSON), 'hermes' (session DB). "
        "Returns the watch_id for subsequent operations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "enum": ["zero", "hermes"],
                    "description": "Agent host to watch",
                },
                "command": {
                    "type": "string",
                    "default": "",
                    "description": "Optional custom command for Zero exec (e.g. 'zero exec --output-format stream-json')",
                },
                "cwd": {
                    "type": "string",
                    "default": "",
                    "description": "Working directory for the watched process",
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "blackbox_watch_stop",
        "description": "Stop an active telemetry watch and finalize the run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "enum": ["zero"],
                    "description": "Host watch to stop",
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "blackbox_ingest",
        "description": "Ingest raw telemetry events (JSON array of TraceEvent dicts) into the blackbox store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of TraceEvent dicts",
                },
            },
            "required": ["events"],
        },
    },
    {
        "name": "blackbox_ingest_zero_jsonl",
        "description": "Parse and ingest a Zero stream-JSON JSONL file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to Zero stream-JSON JSONL file",
                },
                "run_id": {
                    "type": "string",
                    "default": "",
                    "description": "Optional run ID override",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "blackbox_ingest_hermes_session",
        "description": "Ingest a Hermes session from the session DB by session ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "description": "Hermes session ID from blackbox_hermes_sessions",
                },
                "run_id": {
                    "type": "string",
                    "default": "",
                    "description": "Optional run ID override",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "blackbox_runs_list",
        "description": "List recorded blackbox runs with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "default": "",
                    "description": "Filter by host (zero, hermes)",
                },
                "project_key": {
                    "type": "string",
                    "default": "",
                    "description": "Filter by project key",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    {
        "name": "blackbox_run_get",
        "description": "Get full details for a single run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID from blackbox_runs_list",
                },
                "include_events": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include event list",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_run_score",
        "description": "Score a run's context efficiency (11 metrics).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID",
                },
                "archetype": {
                    "type": "string",
                    "enum": ["auto", "research", "debug", "ops", "feature", "edit"],
                    "default": "auto",
                    "description": "Task archetype for tailored scoring. 'auto' = auto-detect",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_run_effectiveness",
        "description": "Score a run's outcome — did the task actually land?",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_run_timeline",
        "description": "Get the causal timeline for a run (compact redacted action trace).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_run_suggest",
        "description": "Generate optimization suggestions for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_run_handoff",
        "description": "Generate a structured handoff summary for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "blackbox_baselines_get",
        "description": "Get per-project baselines for a project and archetype.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_key": {
                    "type": "string",
                    "description": "Project key (repo name)",
                },
                "archetype": {
                    "type": "string",
                    "enum": ["research", "debug", "ops", "feature", "edit"],
                    "default": "feature",
                },
            },
            "required": ["project_key"],
        },
    },
    {
        "name": "blackbox_hermes_sessions",
        "description": "List recent Hermes sessions available for ingestion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "blackbox_stats",
        "description": "Get blackbox store statistics.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "blackbox_install_zero_plugin",
        "description": "Install the eling-blackbox telemetry plugin for Zero agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_dir": {
                    "type": "string",
                    "default": "",
                    "description": "Optional custom target directory for plugin.json",
                },
            },
            "required": [],
        },
    },
]


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    store = _get_store()

    def ok(data: Any) -> dict:
        try:
            text = json.dumps(data, default=str, indent=2)
        except Exception:
            text = json.dumps(
                {"error": "result not serializable", "raw": str(data)[:500]}
            )
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    def err(msg: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32000, "message": msg},
        }

    try:
        # ── Watch ────────────────────────────────────────────────────
        if tool_name == "blackbox_watch_start":
            host = args.get("host", "")
            cmd_str = args.get("command", "")
            cwd = args.get("cwd", "") or None

            if host == "zero":
                global _zero_adapter
                if _zero_adapter and _zero_adapter._running:
                    return ok({"status": "already_running", "host": "zero"})

                def _on_zero_event(ev: TraceEvent):
                    store.ingest(ev)

                _zero_adapter = ZeroAdapter(on_event=_on_zero_event)
                if cmd_str:
                    cmd_parts = cmd_str.split()
                else:
                    cmd_parts = ["zero", "exec", "--output-format", "stream-json"]
                _zero_adapter.watch_stream(cmd_parts, cwd=cwd)
                return ok(
                    {
                        "status": "started",
                        "host": "zero",
                        "command": " ".join(cmd_parts),
                    }
                )

            elif host == "hermes":
                global _hermes_adapter
                _hermes_adapter = HermesAdapter(on_event=lambda ev: store.ingest(ev))
                if not _hermes_adapter.available:
                    return ok(
                        {"status": "error", "message": "Hermes session DB not found"}
                    )
                return ok({"status": "ready", "host": "hermes"})

            else:
                return err(f"Unknown host: {host}")

        elif tool_name == "blackbox_watch_stop":
            host = args.get("host", "zero")
            if host == "zero" and _zero_adapter:
                _zero_adapter.stop()
                summary = _zero_adapter.get_summary()
                store.finalize_run(summary.run_id, summary)
                return ok(
                    {
                        "status": "stopped",
                        "host": "zero",
                        "events_captured": len(_zero_adapter.events),
                    }
                )
            return ok({"status": "not_running", "host": host})

        # ── Ingest ────────────────────────────────────────────────────
        elif tool_name == "blackbox_ingest":
            raw_events = args.get("events", [])
            events = []
            for raw in raw_events:
                raw["type"] = raw.get("type", "")
                ev = TraceEvent.from_dict(raw)
                events.append(ev)
            count = store.ingest_batch(events)
            # Auto-score if session end
            run_ids = set(e.run_id for e in events if e.type == EventType.SESSION_END)
            for run_id in run_ids:
                run_events = [e for e in events if e.run_id == run_id]
                summary = _build_summary_from_events(run_events)
                store.finalize_run(run_id, summary)
            return ok({"ingested": count, "runs_completed": len(run_ids)})

        elif tool_name == "blackbox_ingest_zero_jsonl":
            path = args.get("path", "")
            run_id = args.get("run_id", "")
            if not os.path.exists(path):
                return err(f"File not found: {path}")
            adapter = ZeroAdapter()
            events = adapter.ingest_jsonl(path, run_id=run_id)
            store.ingest_batch(events)
            summary = build_summary_from_zero_events(events)
            store.finalize_run(summary.run_id, summary)
            archetype = EfficiencyScorer.detect_archetype(events, summary)
            summary.archetype = archetype
            return ok(
                {
                    "ingested": len(events),
                    "run_id": summary.run_id,
                    "archetype": archetype.value,
                    "files_read": len(summary.files_read),
                    "edits": summary.edit_count,
                    "commands": summary.bash_count,
                }
            )

        elif tool_name == "blackbox_ingest_hermes_session":
            session_id = args.get("session_id", 0)
            run_id = args.get("run_id", "")
            adapter = HermesAdapter()
            if not adapter.available:
                return err("Hermes session DB not found")
            events = adapter.ingest_session(session_id, run_id=run_id)
            store.ingest_batch(events)
            summary = adapter.build_summary(events)
            store.finalize_run(summary.run_id, summary)
            return ok(
                {
                    "ingested": len(events),
                    "run_id": summary.run_id,
                    "session_id": session_id,
                }
            )

        # ── Query ─────────────────────────────────────────────────────
        elif tool_name == "blackbox_runs_list":
            host = args.get("host", "") or None
            project_key = args.get("project_key", "") or None
            limit = args.get("limit", 20)
            runs = store.list_runs(host=host, project_key=project_key, limit=limit)
            return ok({"runs": runs[:limit]})

        elif tool_name == "blackbox_run_get":
            run_id = args.get("run_id", "")
            include_events = args.get("include_events", False)
            run = store.get_run(run_id)
            if run is None:
                return err(f"Run not found: {run_id}")
            result = dict(run)
            if include_events:
                result["events"] = store.get_events(run_id, limit=1000)
            return ok(result)

        # ── Scoring ───────────────────────────────────────────────────
        elif tool_name == "blackbox_run_score":
            run_id = args.get("run_id", "")
            archetype_str = args.get("archetype", "auto")

            run_data = store.get_run(run_id)
            if run_data is None:
                return err(f"Run not found: {run_id}")

            events_dicts = store.get_events(run_id, limit=5000)
            if not events_dicts:
                return err(f"No events for run: {run_id}")

            events = [TraceEvent.from_dict(ed) for ed in events_dicts]
            summary = _build_summary_from_events(events)

            # Detect or use explicit archetype
            if archetype_str == "auto":
                archetype = EfficiencyScorer.detect_archetype(events, summary)
            else:
                archetype = TaskArchetype(archetype_str)

            scorer = EfficiencyScorer(archetype=archetype)
            report = scorer.score(events, summary)
            store.save_efficiency(run_id, report)

            # Build baselines
            project_key = run_data.get("project_key", "") or summary.project_key
            if project_key:
                existing = store.get_baselines(project_key, archetype.value)
                if existing:
                    existing["runs"] = existing.get("runs", 0) + 1
                    existing["avg_score"] = (
                        existing.get("avg_score", 0) * (existing["runs"] - 1)
                        + report.overall_score
                    ) / existing["runs"]
                    store.save_baselines(project_key, archetype.value, existing)
                else:
                    store.save_baselines(
                        project_key,
                        archetype.value,
                        {
                            "runs": 1,
                            "avg_score": report.overall_score,
                            "archetype": archetype.value,
                        },
                    )

            return ok(
                {
                    "run_id": run_id,
                    "archetype": archetype.value,
                    "overall_score": report.overall_score,
                    "metrics": {
                        "context_pressure": round(report.context_pressure, 3),
                        "cache_hit_ratio": round(report.cache_hit_ratio, 3),
                        "redundant_reads": round(report.redundant_reads, 3),
                        "read_amplification": round(report.read_amplification, 3),
                        "large_injections": round(report.large_injections, 3),
                        "retry_waste": round(report.retry_waste, 3),
                        "yield_density": round(report.yield_density, 3),
                        "tool_overhead": round(report.tool_overhead, 3),
                        "edit_churn": round(report.edit_churn, 3),
                        "large_file_reads": round(report.large_file_reads, 3),
                        "unused_reads": round(report.unused_reads, 3),
                    },
                    "reclaimable_tokens": report.reclaimable_tokens,
                    "offenders": report.offenders,
                }
            )

        elif tool_name == "blackbox_run_effectiveness":
            run_id = args.get("run_id", "")
            events_dicts = store.get_events(run_id, limit=5000)
            if not events_dicts:
                return err(f"No events for run: {run_id}")
            events = [TraceEvent.from_dict(ed) for ed in events_dicts]
            summary = _build_summary_from_events(events)
            scorer = EffectivenessScorer()
            report = scorer.score(events, summary)
            store.save_effectiveness(run_id, report)
            return ok(report.to_dict())

        elif tool_name == "blackbox_run_timeline":
            run_id = args.get("run_id", "")
            events_dicts = store.get_events(run_id, limit=5000)
            if not events_dicts:
                return err(f"No events for run: {run_id}")
            events = [TraceEvent.from_dict(ed) for ed in events_dicts]
            tl = CausalTimeline()
            timeline = tl.build(events)
            return ok(
                {
                    "run_id": run_id,
                    "events_in_timeline": len(timeline),
                    "timeline": timeline,
                }
            )

        elif tool_name == "blackbox_run_suggest":
            run_id = args.get("run_id", "")
            events_dicts = store.get_events(run_id, limit=5000)
            if not events_dicts:
                return err(f"No events for run: {run_id}")
            events = [TraceEvent.from_dict(ed) for ed in events_dicts]
            summary = _build_summary_from_events(events)

            # Get scores
            eff_report = store.get_efficiency(run_id)
            eff_score = 0
            if eff_report is None:
                archetype = EfficiencyScorer.detect_archetype(events, summary)
                scorer = EfficiencyScorer(archetype=archetype)
                eff_report = scorer.score(events, summary)
                store.save_efficiency(run_id, eff_report)
            eff_score = eff_report.overall_score

            # Build suggestions
            suggestions = _build_suggestions(eff_report)
            timeline = CausalTimeline().to_prompt_block(events)

            return ok(
                {
                    "run_id": run_id,
                    "efficiency_score": eff_score,
                    "archetype": eff_report.archetype.value,
                    "suggestions": suggestions,
                    "timeline_summary": timeline,
                }
            )

        elif tool_name == "blackbox_run_handoff":
            run_id = args.get("run_id", "")
            run = store.get_run(run_id)
            if run is None:
                return err(f"Run not found: {run_id}")

            events_dicts = store.get_events(run_id, limit=5000)
            events = [TraceEvent.from_dict(ed) for ed in events_dicts]
            summary = _build_summary_from_events(events)

            handoff = _build_handoff(run, summary, events)
            return ok({"handoff": handoff})

        elif tool_name == "blackbox_baselines_get":
            project_key = args.get("project_key", "")
            archetype = args.get("archetype", "feature")
            baselines = store.get_baselines(project_key, archetype)
            if baselines is None:
                return ok(
                    {
                        "project_key": project_key,
                        "archetype": archetype,
                        "baselines": None,
                        "message": "No baselines yet for this project/archetype",
                    }
                )
            return ok(
                {
                    "project_key": project_key,
                    "archetype": archetype,
                    "baselines": baselines,
                }
            )

        elif tool_name == "blackbox_hermes_sessions":
            limit = args.get("limit", 10)
            adapter = HermesAdapter()
            if not adapter.available:
                return err("Hermes session DB not found")
            sessions = adapter.list_sessions(limit=limit)
            return ok({"sessions": sessions})

        elif tool_name == "blackbox_stats":
            runs = store.list_runs(limit=1000)
            return ok(
                {
                    "total_runs": len(runs),
                    "db_path": str(store.db_path),
                    "runs": runs[:5],
                }
            )

        elif tool_name == "blackbox_install_zero_plugin":
            target_dir = args.get("target_dir", "") or None
            plugin_path = install_zero_plugin(target_dir)
            return ok(
                {
                    "status": "installed",
                    "path": plugin_path,
                    "message": "Zero telemetry plugin installed. Run 'zero plugins install' to activate.",
                }
            )

        else:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"unknown tool: {tool_name}"},
            }

    except Exception as e:
        logger.error("Tool call error: %s", e, exc_info=True)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"},
        }


def _build_summary_from_events(events: list[TraceEvent]) -> RunSummary:
    """Build a RunSummary from a list of events (host-agnostic)."""
    if not events:
        return RunSummary(
            run_id="", session_id="", host="unknown", agent_id="unknown", start_time=0
        )

    first = events[0]
    last = events[-1]
    summary = RunSummary(
        run_id=first.run_id,
        session_id=first.session_id,
        host=first.host,
        agent_id=first.agent_id,
        start_time=first.timestamp,
        end_time=last.timestamp if last.type == EventType.SESSION_END else None,
    )

    for ev in events:
        if ev.type == EventType.READ_FILE:
            summary.read_count += 1
            if ev.file_path:
                summary.files_read.add(ev.file_path)
        elif ev.type in (EventType.WRITE_FILE, EventType.EDIT_FILE):
            if ev.type == EventType.WRITE_FILE:
                summary.write_count += 1
            else:
                summary.edit_count += 1
            if ev.file_path:
                summary.files_written.add(ev.file_path)
        elif ev.type == EventType.BASH:
            summary.bash_count += 1
            if ev.command:
                summary.commands_run.append(ev.command[:80])
        elif ev.type == EventType.TOOL_CALL:
            summary.tool_call_count += 1
        elif ev.type == EventType.SUBAGENT_SPAWN:
            summary.subagent_count += 1
            if ev.subagent_role:
                summary.subagent_roles.append(ev.subagent_role)
        elif ev.type == EventType.ERROR:
            summary.error_count += 1
        elif ev.type == EventType.COMPACT:
            summary.compact_count += 1
        elif ev.type == EventType.USAGE:
            summary.total_prompt_tokens += ev.prompt_tokens or 0
            summary.total_completion_tokens += ev.completion_tokens or 0
            summary.total_tokens += ev.total_tokens or 0
        if ev.context_tokens and ev.context_tokens > summary.peak_context_tokens:
            summary.peak_context_tokens = ev.context_tokens

    archetype = EfficiencyScorer.detect_archetype(events, summary)
    summary.archetype = archetype
    return summary


def _build_suggestions(report: EfficiencyReport) -> list[dict[str, Any]]:
    """Build actionable optimization suggestions from an efficiency report."""
    suggestions = []

    if (
        report.redundant_reads < 0.7
        and report.reclaimable_tokens.get("redundant_reads", 0) > 0
    ):
        tokens = report.reclaimable_tokens["redundant_reads"]
        offenders = report.offenders.get("redundant_reads", [])
        suggestions.append(
            {
                "metric": "redundant_reads",
                "severity": "high" if tokens > 10000 else "medium",
                "reclaimable_tokens": tokens,
                "message": f"Read the same files multiple times (~{tokens} tokens wasted). "
                f"Read each file once and cache it, then re-read only changed line ranges.",
                "offenders": offenders[:5],
            }
        )

    if (
        report.read_amplification < 0.7
        and report.reclaimable_tokens.get("read_amplification", 0) > 0
    ):
        tokens = report.reclaimable_tokens["read_amplification"]
        suggestions.append(
            {
                "metric": "read_amplification",
                "severity": "high" if tokens > 15000 else "medium",
                "reclaimable_tokens": tokens,
                "message": f"Reading far more than editing (~{tokens} tokens excess). "
                f"Use grep/glob + ranged reads instead of pulling whole files.",
            }
        )

    if report.retry_waste < 0.7:
        tokens = report.reclaimable_tokens.get("retry_waste", 0)
        offenders = report.offenders.get("retry_waste", [])
        suggestions.append(
            {
                "metric": "retry_waste",
                "severity": "high",
                "reclaimable_tokens": tokens,
                "message": f"Commands retried before root cause fixed (~{tokens} tokens wasted). "
                f"Investigate the failure before re-running.",
                "offenders": offenders[:5],
            }
        )

    if report.edit_churn < 0.7:
        tokens = report.reclaimable_tokens.get("edit_churn", 0)
        offenders = report.offenders.get("edit_churn", [])
        suggestions.append(
            {
                "metric": "edit_churn",
                "severity": "medium",
                "reclaimable_tokens": tokens,
                "message": f"Same files rewritten many times (~{tokens} tokens churn). "
                f"Plan changes before editing; use sub-agents for exploration.",
                "offenders": offenders[:5],
            }
        )

    if report.context_pressure < 0.5:
        suggestions.append(
            {
                "metric": "context_pressure",
                "severity": "high",
                "reclaimable_tokens": 0,
                "message": "Context window is near capacity. "
                "Use /compact or /compress to summarise resolved turns.",
            }
        )

    return suggestions


def _build_handoff(
    run: dict[str, Any], summary: RunSummary, events: list[TraceEvent]
) -> str:
    """Build a structured handoff Markdown summary."""
    lines = [
        f"# Handoff: {summary.run_id}",
        "",
        f"**Host:** {summary.host}  ",
        f"**Agent:** {summary.agent_id}  ",
        f"**Archetype:** {summary.archetype.value}  ",
        f"**Duration:** {summary.duration():.1f}s  ",
        "",
        "## What happened",
        "",
        f"- Files read: {len(summary.files_read)} ({summary.read_count} reads)",
        f"- Files written: {len(summary.files_written)} ({summary.write_count + summary.edit_count} edits)",
        f"- Commands run: {summary.bash_count}",
        f"- Sub-agents spawned: {summary.subagent_count}",
        f"- Errors: {summary.error_count}",
        f"- Compactions: {summary.compact_count}",
        "",
    ]

    if summary.files_written:
        lines.append("## Files in play")
        lines.append("")
        for f in sorted(summary.files_written):
            lines.append(f"- `{f}`")
        lines.append("")

    if summary.commands_run:
        lines.append("## Commands run")
        lines.append("")
        for c in summary.commands_run[-10:]:
            lines.append(f"- `{c}`")
        lines.append("")

    if summary.subagent_roles:
        lines.append("## Sub-agents")
        lines.append("")
        for r in set(summary.subagent_roles):
            lines.append(f"- {r}")
        lines.append("")

    # Last non-trivial event
    significant = [
        e for e in events if e.type not in (EventType.USAGE, EventType.FINAL)
    ]
    if significant:
        last_ev = significant[-1]
        lines.append("## Last significant action")
        lines.append("")
        lines.append(f"**Type:** {last_ev.type.value}  ")
        if last_ev.file_path:
            lines.append(f"**File:** `{last_ev.file_path}`  ")
        if last_ev.command:
            lines.append(f"**Command:** `{last_ev.command[:100]}`  ")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by eling-blackbox Layer 2*")

    return "\n".join(lines)


# ── MCP protocol handler ──────────────────────────────────────────────────


def _handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params", {})

    try:
        if method == "initialize":
            client_info = params.get("clientInfo", {})
            client_name = client_info.get("name", "unknown")
            logger.info("Blackbox MCP client connected: %s", client_name)
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "eling-blackbox", "version": "0.1.0"},
                },
            }
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
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"unknown method: {method}"},
            }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"},
        }


# ── Stdio entry point ─────────────────────────────────────────────────────


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

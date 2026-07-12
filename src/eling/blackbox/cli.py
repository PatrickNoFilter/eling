"""Blackbox CLI — command-line interface for Layer 2 telemetry.

Subcommands:
  watch       Start watching an agent's telemetry
  ingest      Ingest pre-recorded telemetry
  runs        List and inspect recorded runs
  score       Score a run's context efficiency
  stats       Show blackbox store stats
  install     Install the Zero telemetry plugin
  mcp         Run the MCP server (stdio)
"""

from __future__ import annotations

import argparse
import json
import sys

from .store import BlackboxStore
from .score import EfficiencyScorer
from .adapters.zero import (
    ZeroAdapter,
    build_summary_from_zero_events,
    install_zero_plugin,
)
from .adapters.hermes import HermesAdapter
from .core import TraceEvent, TaskArchetype


def _build_summary_from_events(events):
    """Reused helper."""
    from .mcp_server import _build_summary_from_events as fn

    return fn(events)


def cmd_watch(args: argparse.Namespace) -> None:
    """Start watching agent telemetry."""
    host = args.host
    command = args.command

    store = BlackboxStore()

    if host == "zero":
        adapter = ZeroAdapter(on_event=lambda ev: store.ingest(ev))
        cmd_parts = (
            command.split()
            if command
            else ["zero", "exec", "--output-format", "stream-json"]
        )
        print(f"Watching Zero ({' '.join(cmd_parts)})...")
        print(f"Events will be stored in: {store.db_path}")
        try:
            adapter.watch_stream(cmd_parts, cwd=args.cwd)
            # Block until interrupted
            import signal

            signal.pause()
        except KeyboardInterrupt:
            adapter.stop()
            summary = adapter.get_summary()
            store.finalize_run(summary.run_id, summary)
            print(f"\nStopped. Captured {len(adapter.events)} events.")
            print(f"Run ID: {summary.run_id}")

    elif host == "hermes":
        adapter = HermesAdapter()
        if not adapter.available:
            print("Error: Hermes session DB not found")
            sys.exit(1)
        print("Hermes adapter ready. Use '--session-id' to ingest a session.")
        sessions = adapter.list_sessions(limit=10)
        print(f"\nRecent sessions ({len(sessions)}):")
        for s in sessions:
            print(
                f"  [{s['id']}] {s.get('title', 'untitled')} ({s.get('message_count', 0)} msgs)"
            )
    else:
        print(f"Unknown host: {host}")
        sys.exit(1)


def cmd_ingest(args: argparse.Namespace) -> None:
    """Ingest telemetry from a file or Hermes session."""
    store = BlackboxStore()

    if args.zero_jsonl:
        adapter = ZeroAdapter()
        events = adapter.ingest_jsonl(args.zero_jsonl, run_id=args.run_id or "")
        store.ingest_batch(events)
        summary = build_summary_from_zero_events(events)
        store.finalize_run(summary.run_id, summary)
        print(f"Ingested {len(events)} events from {args.zero_jsonl}")
        print(f"Run ID: {summary.run_id}")
        print(f"Archetype: {summary.archetype.value}")

    if args.hermes_session:
        adapter = HermesAdapter()
        if not adapter.available:
            print("Error: Hermes session DB not found")
            sys.exit(1)
        events = adapter.ingest_session(args.hermes_session, run_id=args.run_id or "")
        store.ingest_batch(events)
        summary = adapter.build_summary(events)
        store.finalize_run(summary.run_id, summary)
        print(
            f"Ingested {len(events)} events from Hermes session {args.hermes_session}"
        )


def cmd_runs(args: argparse.Namespace) -> None:
    """List or inspect runs."""
    store = BlackboxStore()

    if args.run_id:
        run = store.get_run(args.run_id)
        if run is None:
            print(f"Run not found: {args.run_id}")
            sys.exit(1)
        print(json.dumps(run, indent=2, default=str))

        # Optionally show events
        if args.events:
            events = store.get_events(args.run_id, limit=50)
            print(f"\nEvents ({len(events)} shown):")
            for ev in events[:50]:
                print(
                    f"  [{ev['type']}] {ev.get('file_path') or ev.get('tool_name') or ev.get('command', '')[:60]}"
                )
    else:
        runs = store.list_runs(
            host=args.host or None,
            project_key=args.project or None,
            limit=args.limit,
        )
        print(f"Runs ({len(runs)}):")
        for r in runs:
            arch = r.get("archetype", "?")
            score = store.get_efficiency(r["run_id"])
            score_str = f" score={score.overall_score}" if score else ""
            print(f"  [{r['run_id'][:20]}...] {r.get('host', '?')} {arch}{score_str}")


def cmd_score(args: argparse.Namespace) -> None:
    """Score a run's context efficiency."""
    store = BlackboxStore()
    run_id = args.run_id

    events_dicts = store.get_events(run_id, limit=5000)
    if not events_dicts:
        print(f"No events for run: {run_id}")
        sys.exit(1)

    events = [TraceEvent.from_dict(ed) for ed in events_dicts]
    summary = _build_summary_from_events(events)

    if args.archetype == "auto":
        archetype = EfficiencyScorer.detect_archetype(events, summary)
    else:
        archetype = TaskArchetype(args.archetype)

    scorer = EfficiencyScorer(archetype=archetype)
    report = scorer.score(events, summary)
    store.save_efficiency(run_id, report)

    print(f"Run: {run_id}")
    print(f"Archetype: {archetype.value}")
    print(f"Overall score: {report.overall_score}/100")
    print()
    for metric, weight in scorer._weights.items():
        val = getattr(report, metric, 0)
        bar = "█" * int(val * 20) + "░" * (20 - int(val * 20))
        print(f"  {metric:25s} {bar} {val:.2f}")

    if report.reclaimable_tokens:
        total = sum(report.reclaimable_tokens.values())
        print(f"\nReclaimable tokens: ~{total}")
        for metric, tokens in report.reclaimable_tokens.items():
            if tokens > 0:
                offenders = report.offenders.get(metric, [])
                off_str = f" ({', '.join(offenders[:3])})" if offenders else ""
                print(f"  {metric:25s}: ~{tokens}{off_str}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show blackbox store statistics."""
    store = BlackboxStore()
    runs = store.list_runs(limit=100)
    print(f"Blackbox store: {store.db_path}")
    print(f"Total runs: {len(runs)}")
    print()
    if runs:
        print("Recent runs:")
        for r in runs[:10]:
            print(
                f"  {r['run_id'][:24]:24s} {r.get('host', '?'):12s} {r.get('archetype', '?'):12s}"
            )


def cmd_install(args: argparse.Namespace) -> None:
    """Install the Zero telemetry plugin."""
    target = args.target_dir
    path = install_zero_plugin(target)
    print(f"Zero telemetry plugin installed at: {path}")
    print("Run 'zero plugins install' to activate it.")


def cmd_mcp(args: argparse.Namespace) -> None:
    """Run the MCP server (stdio)."""
    from .mcp_server import run_stdio

    run_stdio()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Eling Blackbox — Layer 2 flight recorder for coding agents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # watch
    wp = sub.add_parser("watch", help="Start watching agent telemetry")
    wp.add_argument("host", choices=["zero", "hermes"])
    wp.add_argument("--command", "-c", default="", help="Stream-JSON command for Zero")
    wp.add_argument("--cwd", default=None, help="Working directory")
    wp.set_defaults(func=cmd_watch)

    # ingest
    ip = sub.add_parser("ingest", help="Ingest telemetry data")
    ip.add_argument("--zero-jsonl", help="Path to Zero stream-JSON JSONL file")
    ip.add_argument("--hermes-session", type=int, help="Hermes session ID")
    ip.add_argument("--run-id", default="", help="Override run ID")
    ip.set_defaults(func=cmd_ingest)

    # runs
    rp = sub.add_parser("runs", help="List and inspect runs")
    rp.add_argument("run_id", nargs="?", default="", help="Specific run ID")
    rp.add_argument("--host", default="", help="Filter by host")
    rp.add_argument("--project", default="", help="Filter by project key")
    rp.add_argument("--limit", type=int, default=20)
    rp.add_argument("--events", action="store_true", help="Show events for a run")
    rp.set_defaults(func=cmd_runs)

    # score
    sp = sub.add_parser("score", help="Score a run's context efficiency")
    sp.add_argument("run_id", help="Run ID to score")
    sp.add_argument(
        "--archetype",
        choices=["auto", "research", "debug", "ops", "feature", "edit"],
        default="auto",
    )
    sp.set_defaults(func=cmd_score)

    # stats
    stp = sub.add_parser("stats", help="Show blackbox store statistics")
    stp.set_defaults(func=cmd_stats)

    # install
    inp = sub.add_parser("install", help="Install the Zero telemetry plugin")
    inp.add_argument("--target-dir", default="", help="Custom plugin directory")
    inp.set_defaults(func=cmd_install)

    # mcp
    mp = sub.add_parser("mcp", help="Run the MCP server (stdio)")
    mp.set_defaults(func=cmd_mcp)

    parsed = parser.parse_args(argv)
    parsed.func(parsed)


def run_cli() -> None:
    main()


if __name__ == "__main__":
    main()

"""Eling CLI — eling remember / recall / reason / stats / mcp / config."""

import argparse
import json
import os
import sys

from .brain import Brain
from .config import DEFAULTS, describe_config, get_config, remove_config_key, resolve_config, set_config_key


def main():
    parser = argparse.ArgumentParser(prog="eling", description="Eling — unified second brain")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rem = sub.add_parser("remember", help="Store content")
    p_rem.add_argument("content")
    p_rem.add_argument("--layer", default="auto", choices=["auto", "facts", "kb", "notion"])
    p_rem.add_argument("--category", default="general")
    p_rem.add_argument("--tags", default="")
    p_rem.add_argument("--title", default="")
    p_rem.add_argument("--source", default="mcp", help="Agent origin (hermes, opencode, etc.)")

    p_rec = sub.add_parser("recall", help="Search across all layers")
    p_rec.add_argument("query")
    p_rec.add_argument("--limit", type=int, default=10)
    p_rec.add_argument("--layers", default="", help="comma-separated subset")
    p_rec.add_argument("--source", default="", help="Filter by agent origin (hermes, opencode, etc.)")

    p_probe = sub.add_parser("probe", help="Get facts about an entity")
    p_probe.add_argument("entity")
    p_probe.add_argument("--limit", type=int, default=10)

    p_reason = sub.add_parser("reason", help="Compositional entity query")
    p_reason.add_argument("entities", nargs="+")
    p_reason.add_argument("--limit", type=int, default=10)

    p_reflect = sub.add_parser("reflect", help="Promote fact_id to Notion")
    p_reflect.add_argument("fact_id", type=int)

    sub.add_parser("stats", help="Show brain stats")

    p_mcp = sub.add_parser("mcp", help="Run MCP server (stdio)")
    p_mcp.add_argument("--transport", default="stdio")

    # ── config subcommand ──
    p_cfg = sub.add_parser("config", help="Manage Eling configuration")
    p_cfg_cmd = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_cfg_get = p_cfg_cmd.add_parser("get", help="Get a config value")
    p_cfg_get.add_argument("key", nargs="?", default="", help="Config key (omit to see all)")
    p_cfg_set = p_cfg_cmd.add_parser("set", help="Set a config value")
    p_cfg_set.add_argument("key", help="Config key")
    p_cfg_set.add_argument("value", help="Config value")
    p_cfg_set.add_argument("--home", default="", help="Eling home dir (default: resolved)")
    p_cfg_ls = p_cfg_cmd.add_parser("ls", help="List all config keys with values and sources")
    p_cfg_unset = p_cfg_cmd.add_parser("unset", help="Remove a config key")
    p_cfg_unset.add_argument("key", help="Config key")
    p_cfg_unset.add_argument("--home", default="", help="Eling home dir (default: resolved)")
    p_cfg_init = p_cfg_cmd.add_parser("init", help="Write default config.json")
    p_cfg_init.add_argument("--home", default="", help="Eling home dir (default: resolved)")
    p_cfg_schema = p_cfg_cmd.add_parser("schema", help="Show config schema")

    # ── sync subcommand ──
    p_sync = sub.add_parser("sync", help="Synchronize layers (facts↔Notion, flush)")
    p_sync.add_argument("--direction", default="all",
                        choices=["push", "pull", "flush", "all"],
                        help="Sync direction [all]")
    p_sync.add_argument("--layer", default="auto",
                        choices=["auto", "facts", "notion", "kb"],
                        help="Layer scope [auto]")
    p_sync.add_argument("--daemon", action="store_true",
                        help="Run as daemon (continuous sync)")
    p_sync.add_argument("--interval", type=int, default=300,
                        help="Daemon interval in seconds [300]")
    p_sync.add_argument("--once", action="store_true",
                        help="Run once and exit [default]")
    p_sync.add_argument("--state-file", default="",
                        help="Path to sync state file")

    args = parser.parse_args()

    if args.cmd == "mcp":
        from .mcp_server import run_stdio
        run_stdio()
        return

    if args.cmd == "config":
        _run_config(args)
        return

    if args.cmd == "sync":
        _run_sync(args)
        return

    brain = Brain()
    try:
        if args.cmd == "remember":
            out = brain.remember(args.content, layer=args.layer, category=args.category,
                                  tags=args.tags, title=args.title, source=args.source)
        elif args.cmd == "recall":
            layers = [s.strip() for s in args.layers.split(",") if s.strip()] or None
            source = args.source or None
            out = brain.recall(args.query, layers=layers, limit=args.limit, source=source)
        elif args.cmd == "probe":
            out = brain.probe(args.entity, limit=args.limit)
        elif args.cmd == "reason":
            out = brain.reason(args.entities, limit=args.limit)
        elif args.cmd == "reflect":
            out = brain.reflect(args.fact_id)
        elif args.cmd == "stats":
            out = brain.stats()
        print(json.dumps(out, indent=2, default=str))
    finally:
        brain.close()


def _run_config(args: argparse.Namespace) -> None:
    """Dispatch config subcommands."""
    if args.config_cmd == "schema":
        print(json.dumps(describe_config(), indent=2))
        return

    if args.config_cmd == "init":
        home = args.home or resolve_config().get("home") or os.path.expanduser("~/.eling")
        for k, v in DEFAULTS.items():
            set_config_key(k, v, home=home)
        print(f"Default config written to {home}/config.json")
        return

    if args.config_cmd == "ls":
        resolved = resolve_config()
        home_dir = resolved.get("home", "?")
        disk = get_config(home=home_dir)
        schema = describe_config()
        print(f"{'KEY':<25} {'VALUE':<20} {'SOURCE':<12}  {'DEFAULT'}")
        print("-" * 80)
        for k in sorted(DEFAULTS):
            val = resolved.get(k, "")
            env = schema[k]["env"]
            src = "default"
            if env and os.environ.get(env):
                src = "env"
            elif k in disk:
                src = "disk"
            elif _hermes_config_has(k):
                src = "hermes"
            v_str = str(val)[:19]
            print(f"{k:<25} {v_str:<20} {src:<12}  {DEFAULTS[k]}")
        return

    if args.config_cmd == "get":
        home = resolve_config().get("home") or ""
        disk = get_config(home=home)
        resolved = resolve_config()
        key = args.key
        if not key:
            print(json.dumps(resolved, indent=2))
            return
        if key == "home":
            print(resolved.get("home", ""))
        elif key in resolved:
            print(resolved[key])
        elif key in disk:
            print(disk[key])
        else:
            print(f"Unknown key: {key}")
        return

    if args.config_cmd == "set":
        home = args.home or resolve_config().get("home") or os.path.expanduser("~/.eling")
        set_config_key(args.key, args.value, home=home)
        print(f"Set {args.key} = {args.value} in {home}/config.json")
        return

    if args.config_cmd == "unset":
        home = args.home or resolve_config().get("home") or os.path.expanduser("~/.eling")
        remove_config_key(args.key, home=home)
        print(f"Removed {args.key} from {home}/config.json")
        return


def _hermes_config_has(key: str) -> bool:
    """Check if key exists in Hermes plugins.eling config."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        pc = cfg_get(cfg, "plugins", "eling", default={}) or {}
        return key in pc
    except Exception:
        return False


def _run_sync(args: argparse.Namespace) -> None:
    """Run sync once or as daemon."""
    brain = Brain()
    try:
        state_file = args.state_file or ""
        if not state_file:
            home = brain.home
            state_file = str(home / "sync_state.json") if hasattr(home, "__truediv__") else ""

        if args.daemon:
            import time

            print(f"🔁 Eling sync daemon — interval {args.interval}s (Ctrl+C to stop)")
            while True:
                result = brain.sync(
                    direction=args.direction,
                    layer=args.layer,
                    sync_state_path=state_file or None,
                )
                pushed = result["pushed"]
                pulled = result["pulled"]
                errors = result.get("errors", [])
                ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
                if errors:
                    print(f"  [{ts}] pushed={pushed} pulled={pulled} errors={errors}")
                else:
                    print(f"  [{ts}] pushed={pushed} pulled={pulled} ✅")
                time.sleep(args.interval)
        else:
            result = brain.sync(
                direction=args.direction,
                layer=args.layer,
                sync_state_path=state_file or None,
            )
            print(json.dumps(result, indent=2, default=str))
    finally:
        brain.close()


if __name__ == "__main__":
    main()

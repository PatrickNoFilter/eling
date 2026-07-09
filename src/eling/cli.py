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

    sub.add_parser("link-stats", help="Zettelkasten link graph statistics")
    sub.add_parser("stats", help="Show brain stats")

    p_linked = sub.add_parser("linked-facts", help="Get facts linked to a fact_id")
    p_linked.add_argument("fact_id", type=int)
    p_linked.add_argument("--limit", type=int, default=10)

    p_evolve = sub.add_parser("evolve", help="Merge near-duplicate facts (memory evolution)")
    p_evolve.add_argument("--threshold", type=float, default=None,
                          help="Jaccard similarity threshold (default: 0.65)")

    # ── snapshot / rollback ──
    p_snap = sub.add_parser("snapshot", help="Create a named snapshot of the facts database")
    p_snap.add_argument("--reason", default="", help="Why the snapshot is taken")

    sub.add_parser("list-snapshots", help="List all available snapshots")

    p_roll = sub.add_parser("rollback", help="Rollback facts database to a snapshot")
    p_roll.add_argument("snapshot_id", help="Snapshot ID to restore (use list-snapshots to find it)")

    p_mcp = sub.add_parser("mcp", help="Run MCP server (stdio)")
    p_mcp.add_argument("--transport", default="stdio")

    # ── continuum subcommand (Layer 6 orchestration tier) ──
    p_cont = sub.add_parser("continuum", help="Continuum Layer 6 — orchestration MCP server")
    p_cont_cmd = p_cont.add_subparsers(dest="continuum_cmd", required=True)
    p_cont_mcp = p_cont_cmd.add_parser("mcp", help="Run the Continuum orchestration MCP server (stdio)")
    p_cont_mcp.add_argument("--db", default="", help="Path to continuum.db (default: ELING_HOME/continuum.db)")

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

    # ── install-opencode subcommand ──
    p_io = sub.add_parser("install-opencode", help="Install eling memory plugin into OpenCode")
    p_io.add_argument("--dry-run", action="store_true",
                      help="Show what would be done without making changes")

    # ── install-zero subcommand ──
    p_iz = sub.add_parser("install-zero", help="Install eling hooks and skill into Zero")
    p_iz.add_argument("--dry-run", action="store_true",
                      help="Show what would be done without making changes")
    p_iz.add_argument("--zero-config-dir", default="",
                      help="Zero config directory (default: ~/.config/zero)")

    # ── install-termux subcommand ──
    p_it = sub.add_parser("install-termux",
                          help="Install eling launcher scripts for Termux on Android")
    p_it.add_argument("--bin-dir", default="",
                      help="Target bin directory (default: ~/.local/bin)")
    p_it.add_argument("--configure-zero", action="store_true",
                      help="Also update Zero MCP config to use the Termux scripts")
    p_it.add_argument("--zero-config-dir", default="",
                      help="Zero config directory (default: ~/.config/zero)")
    p_it.add_argument("--dry-run", action="store_true",
                      help="Show what would be done without making changes")

    # ── init-rules subcommand ──
    p_rules = sub.add_parser("init-rules", help="Write steering rules for AI agents")
    p_rules.add_argument("--project-dir", default=".",
                         help="Project root directory (default: cwd)")
    p_rules.add_argument("--agent", choices=["cursor", "claude_code", "opencode", "generic"], action="append",
                         help="Target agent type (auto-detected if omitted)")
    p_rules.add_argument("--dry-run", action="store_true",
                         help="Show what would be done without making changes")

    args = parser.parse_args()

    if args.cmd == "mcp":
        from .mcp_server import run_stdio
        run_stdio()
        return

    if args.cmd == "continuum":
        if args.continuum_cmd == "mcp":
            if args.db:
                os.environ["ELING_CONTINUUM_DB"] = args.db
            from .continuum.mcp_server import run_stdio as continuum_run
            continuum_run()
        return

    if args.cmd == "config":
        _run_config(args)
        return

    if args.cmd == "sync":
        _run_sync(args)
        return

    if args.cmd == "install-opencode":
        _run_install_opencode(args)
        return

    if args.cmd == "install-zero":
        _run_install_zero(args)
        return

    if args.cmd == "install-termux":
        _run_install_termux(args)
        return

    if args.cmd == "init-rules":
        _run_init_rules(args)
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
        elif args.cmd == "snapshot":
            out = brain.snapshot(reason=args.reason)
        elif args.cmd == "list-snapshots":
            out = {"snapshots": brain.list_snapshots()}
        elif args.cmd == "rollback":
            out = brain.rollback(args.snapshot_id)
        elif args.cmd == "link-stats":
            out = brain.link_stats()
        elif args.cmd == "linked-facts":
            out = brain.linked_facts(args.fact_id, limit=args.limit)
        elif args.cmd == "evolve":
            out = brain.evolve(threshold=args.threshold)
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


def _run_install_opencode_cli() -> None:
    """Console_scripts entry point: install eling plugin into OpenCode."""
    p = argparse.ArgumentParser(prog="eling-install-opencode",
                                description="Install eling memory plugin into OpenCode")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be done without making changes")
    args = p.parse_args()
    _run_install_opencode(args)


def _run_continuum_cli() -> None:
    """Console_scripts entry point: run the Continuum Layer 6 MCP server."""
    p = argparse.ArgumentParser(prog="eling-continuum",
                                description="Eling Continuum — orchestration MCP server")
    p.add_argument("--db", default="", help="Path to continuum.db (default: ELING_HOME/continuum.db)")
    args = p.parse_args()
    if args.db:
        os.environ["ELING_CONTINUUM_DB"] = args.db
    from .continuum.mcp_server import run_stdio as continuum_run
    continuum_run()


def _run_install_opencode(args: argparse.Namespace) -> None:
    """Install the eling memory plugin into OpenCode's plugin directory.

    Detects OpenCode by checking OPENCODE_HOME env var, then
    ~/.config/opencode/, then ~/.opencode/. Copies the bundled
    eling-memory.js plugin and registers it in opencode.jsonc.
    """
    import json
    import shutil
    from pathlib import Path
    from importlib.resources import files as pkg_files

    # 1. Detect opencode config dir
    oc_home = os.environ.get("OPENCODE_HOME")
    if oc_home:
        oc_dir = Path(oc_home)
    else:
        candidates = [
            Path.home() / ".config" / "opencode",
            Path.home() / ".opencode",
        ]
        oc_dir = None
        for c in candidates:
            if c.is_dir():
                oc_dir = c
                break
        if oc_dir is None:
            print("OpenCode config directory not found.", file=sys.stderr)
            print("Checked: OPENCODE_HOME, ~/.config/opencode, ~/.opencode", file=sys.stderr)
            print("Install OpenCode first, or set OPENCODE_HOME.", file=sys.stderr)
            sys.exit(1)

    plugins_dir = oc_dir / "plugins"
    target = plugins_dir / "eling-memory.js"
    config_file = oc_dir / "opencode.jsonc"

    # 2. Locate the bundled plugin JS
    try:
        src = pkg_files("eling.opencode_plugin").joinpath("eling-memory.js")
    except (ModuleNotFoundError, TypeError):
        # Fallback for editable/dev installs
        src = Path(__file__).parent / "opencode_plugin" / "eling-memory.js"

    if not src.exists():
        print(f"Plugin source not found at {src}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[dry-run] Would copy {src} → {target}")
        print(f"[dry-run] Would register plugin in {config_file}")
        return

    # 3. Copy plugin
    plugins_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(target))
    print(f"Copied plugin: {src} → {target}")

    # 4. Register in opencode.jsonc
    rel_path = f"./plugins/eling-memory.js"
    if config_file.exists():
        raw = config_file.read_text(encoding="utf-8")
        # Check if already registered
        if rel_path in raw:
            print(f"Plugin already registered in {config_file}")
        else:
            try:
                # Parse JSONC (strip comments for simple cases)
                lines = raw.splitlines()
                clean_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("//") or stripped.startswith("/*"):
                        continue
                    if "//" in stripped and not stripped.startswith("{"):
                        idx = stripped.find("//")
                        clean_lines.append(stripped[:idx])
                    else:
                        clean_lines.append(line)
                cfg = json.loads("\n".join(clean_lines))
            except json.JSONDecodeError:
                print(f"Could not parse {config_file} — add plugin manually:", file=sys.stderr)
                print(f'  "plugin": ["{rel_path}"]', file=sys.stderr)
                return

            plugins = cfg.get("plugin", [])
            if rel_path not in plugins:
                plugins.append(rel_path)
                cfg["plugin"] = plugins
                # Write back preserving .jsonc extension
                config_file.write_text(
                    json.dumps(cfg, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"Registered plugin in {config_file}")
            else:
                print(f"Plugin already registered in {config_file}")
    else:
        # Create config file
        cfg = {
            "plugin": [rel_path],
        }
        config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"Created {config_file} with plugin registration")

    print("Done. Restart OpenCode to load the eling memory plugin.")


def _run_install_zero_cli() -> None:
    """Console_scripts entry point: install eling into Zero."""
    p = argparse.ArgumentParser(prog="eling-install-zero",
                                description="Install eling hooks and skill into Zero")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be done without making changes")
    p.add_argument("--zero-config-dir", default="",
                   help="Zero config directory (default: ~/.config/zero)")
    args = p.parse_args()
    _run_install_zero(args)


def _run_install_zero(args: argparse.Namespace) -> None:
    """Install eling hooks + skill + MCP server into Zero.

    1. Copy hook script to ~/.zero/scripts/eling-hook.py
    2. Register hooks via `zero hooks add`
    3. Install skill to ~/.local/share/zero/skills/eling/SKILL.md
    4. Add MCP server to ~/.config/zero/config.json
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from importlib.resources import files as pkg_files

    # 1. Detect Zero config dir
    zero_config_arg = args.zero_config_dir
    if zero_config_arg:
        zero_cfg = Path(zero_config_arg)
    else:
        zero_cfg = Path.home() / ".config" / "zero"
    zero_data = Path.home() / ".local" / "share" / "zero"
    zero_scripts = Path.home() / ".zero" / "scripts"

    if args.dry_run:
        print(f"[dry-run] Zero config dir: {zero_cfg}")
        print(f"[dry-run] Zero data dir: {zero_data}")
        print(f"[dry-run] Zero scripts dir: {zero_scripts}")
    else:
        print(f"Zero config dir: {zero_cfg}")
        print(f"Zero data dir: {zero_data}")
        print(f"Zero scripts dir: {zero_scripts}")

    # 2. Locate bundled files
    try:
        pkg = pkg_files("eling.zero_plugin")
    except (ModuleNotFoundError, TypeError):
        pkg = Path(__file__).parent / "zero_plugin"

    hook_src = pkg / "eling-hook.py"
    skill_src = pkg / "SKILL.md"

    if isinstance(pkg, Path):
        hook_src = pkg / "eling-hook.py"
        skill_src = pkg / "SKILL.md"

    for f in (hook_src, skill_src):
        if not f.exists():
            print(f"Error: {f} not found", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        # Show what would be installed
        skill_dst = zero_data / "skills" / "eling" / "SKILL.md"
        hook_dst = zero_scripts / "eling-hook.py"
        print(f"[dry-run] Would copy: {hook_src} → {hook_dst}")
        print(f"[dry-run] Would copy: {skill_src} → {skill_dst}")
        print(f"[dry-run] Would add MCP server to {zero_cfg / 'config.json'}")
        print("[dry-run] Would register hooks: sessionStart, sessionEnd, beforeTool, afterTool")
        print(f"[dry-run] Would install skill: eling → {skill_dst}")
        return

    # 3. Copy hook script
    zero_scripts.mkdir(parents=True, exist_ok=True)
    hook_dst = zero_scripts / "eling-hook.py"
    shutil.copy2(str(hook_src), str(hook_dst))
    os.chmod(str(hook_dst), 0o755)
    print(f"Copied hook script: {hook_src} → {hook_dst}")

    # 4. Register hooks via `zero hooks add`
    hook_registrations = [
        ("eling-sessionstart", "sessionStart",
         "Eling session start — warm caches"),
        ("eling-sessionend", "sessionEnd",
         "Eling session end — flush memory"),
        ("eling-beforetool", "beforeTool",
         "Eling pre-tool — recall context"),
        ("eling-aftool", "afterTool",
         "Eling after-tool — store results"),
    ]

    for hook_id, event, desc in hook_registrations:
        cmd = [
            "zero", "hooks", "add", hook_id,
            "--event", event,
            "--command", f"python3 {hook_dst}",
            "--description", desc,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Registered hook: {hook_id} ({event})")
        else:
            output = (result.stderr or result.stdout or "").strip()
            # Hook may already exist — check
            if "already exists" in output.lower() or "exists" in output.lower():
                print(f"Hook already exists: {hook_id} ({event})")
            else:
                print(f"Warning: hook registration failed for {hook_id}: {output}", file=sys.stderr)

    # 5. Install skill
    skill_dir = zero_data / "skills" / "eling"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dst = skill_dir / "SKILL.md"
    shutil.copy2(str(skill_src), str(skill_dst))
    print(f"Installed skill: {skill_src} → {skill_dst}")

    # 6. Add MCP server to Zero config if not already present
    if zero_cfg.joinpath("config.json").exists():
        try:
            cfg = json.loads(zero_cfg.joinpath("config.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
        mcp = cfg.get("mcp", {})
        # Register BOTH servers — `eling` (Notion, online) + `as_brain`
        # (local memory layers). Zero needs the local brain, so as_brain
        # must be present, not just the Notion-only eling server.
        if "eling" not in mcp:
            mcp["eling"] = {
                "command": "python3",
                "args": ["-m", "eling.mcp_server"],
            }
        if "as_brain" not in mcp:
            mcp["as_brain"] = {
                "command": "python3",
                "args": ["-m", "eling.as_brain.mcp_server"],
            }
        cfg["mcp"] = mcp
        zero_cfg.joinpath("config.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )
        print("Added MCP servers 'eling' + 'as_brain' to Zero config")
    else:
        # Create config file
        cfg = {
            "mcp": {
                "eling": {
                    "command": "python3",
                    "args": ["-m", "eling.mcp_server"],
                },
                "as_brain": {
                    "command": "python3",
                    "args": ["-m", "eling.as_brain.mcp_server"],
                },
            }
        }
        zero_cfg.mkdir(parents=True, exist_ok=True)
        zero_cfg.joinpath("config.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Created Zero config with MCP server: {zero_cfg / 'config.json'}")

    print("\n✅ Eling is now installed in Zero. Restart Zero to load hooks and skill.")


def _run_init_rules(args: argparse.Namespace) -> None:
    """Write steering rules for AI agents."""
    from .rules import write_rules, detect_agent

    project_dir = os.path.abspath(args.project_dir)
    agents = args.agent or None  # None = auto-detect

    if not os.path.isdir(project_dir):
        print(f"Project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    if agents is None:
        agents_sniffed = detect_agent(Path(project_dir))
        print(f"Detected agents: {', '.join(agents_sniffed) if agents_sniffed else '(none)'}")
        if not agents_sniffed:
            agents = ["generic"]
            print("No agent config detected — writing generic ELING_MEMORY.md")

    if args.dry_run:
        results = write_rules(project_dir, agents=agents, dry_run=True)
        print("[dry-run] Would write:")
        for r in results:
            print(f"  [{r['agent']}] {r['action']}: {r['file']}")
        return

    results = write_rules(project_dir, agents=agents)
    for r in results:
        print(f"  [{r['agent']}] {r['action']}: {r['file']}")

    print("Done. Restart your AI agent to load the steering rules.")


def _run_install_termux_cli() -> None:
    """Console_scripts entry point: install eling launchers for Termux."""
    p = argparse.ArgumentParser(prog="eling-install-termux",
                                description="Install eling launcher scripts for Termux on Android")
    p.add_argument("--bin-dir", default="",
                   help="Target bin directory (default: ~/.local/bin)")
    p.add_argument("--configure-zero", action="store_true",
                   help="Also update Zero MCP config to use the Termux scripts")
    p.add_argument("--zero-config-dir", default="",
                   help="Zero config directory (default: ~/.config/zero)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be done without making changes")
    args = p.parse_args()
    _run_install_termux(args)


def _run_install_termux(args: argparse.Namespace) -> None:
    """Install eling launcher scripts for Termux on Android.

    On Termux, ``#!/usr/bin/env python3`` does not work because Termux has no
    ``/usr/bin/env`` — the correct interpreter path is
    ``/data/data/com.termux/files/usr/bin/env``.

    This command writes three wrapper scripts in ``~/.local/bin/`` (or the
    directory given by ``--bin-dir``) using the Termux-compatible shebang:

    +-------------------------+--------------------------------------------------+
    | Script                  | Purpose                                          |
    +-------------------------+--------------------------------------------------+
    | ``eling-termux``        | CLI wrapper — delegates to ``eling.cli.main``    |
    | ``eling-termux-mcp``    | Notion-only MCP server (6 tools)                 |
    | ``as-brain-mcp``        | Local memory MCP server — facts, KB, code, etc.  |
    +-------------------------+--------------------------------------------------+

    With ``--configure-zero`` it also updates Zero's ``config.json`` to point
    at these scripts instead of bare ``python3 -m`` invocations.
    """
    import stat
    from pathlib import Path

    dry_run = getattr(args, "dry_run", False)
    bin_dir = Path(args.bin_dir or Path.home() / ".local" / "bin")
    termux_env = "/data/data/com.termux/files/usr/bin/env"
    shebang = f"#!{termux_env} python3"

    # ── Detect Termux ──
    if not os.path.isdir("/data/data/com.termux/files/usr/bin"):
        print("⚠️  This system does not appear to be Termux (no /data/data/com.termux/files/usr/bin).")
        print("   The scripts will still be written but the shebang may not work on other platforms.\n")

    scripts = {
        "eling-termux": f'''{shebang}
"""Eling Termux CLI — zero-fuss launcher for Termux on Android.

Usage:
  eling-termux remember "content" --category config
  eling-termux recall "query"
  eling-termux stats
  eling-termux mcp          ← run MCP server (stdio)
  eling-termux help
"""

import os
import sys

# Force ELING_HOME to persistent location
os.environ.setdefault("ELING_HOME", os.path.expanduser("~/.eling"))
os.makedirs(os.environ["ELING_HOME"], exist_ok=True)

# In Termux we skip CodeLayer auto-index (no project scanning)
os.environ["ELING_NO_CODE_INDEX"] = "1"

if len(sys.argv) > 1 and sys.argv[1] == "mcp":
    # Launch MCP server
    from eling.mcp_server import run_stdio
    run_stdio()
elif len(sys.argv) > 1 and sys.argv[1] == "help":
    print(__doc__)
else:
    # Delegate to eling CLI
    from eling.cli import main
    sys.argv[0] = "eling"
    main()
''',
        "eling-termux-mcp": f'''{shebang}
"""Eling MCP for Zero in Termux — notion-only memory layer.

This server only handles Notion operations.
For local memory layers (facts, KB, code, builtin, HRR),
use the `as-brain-mcp` launcher instead.
"""
import os, sys

os.environ.setdefault("ELING_HOME", os.path.expanduser("~/.eling"))
os.makedirs(os.environ["ELING_HOME"], exist_ok=True)

# Change to a temp dir so CodeLayer auto-index doesn't scan home
import tempfile
tmpdir = tempfile.mkdtemp(prefix="eling-zero-")
os.chdir(tmpdir)

# Launch MCP server
from eling.mcp_server import run_stdio
run_stdio()
''',
        "as-brain-mcp": f'''{shebang}
"""As Brain MCP launcher — local memory layers for agents.

Serves facts, KB, code, builtin, and HRR layers via MCP.
Notion sync is handled separately by `eling-mcp`.
"""
import os, sys

os.environ.setdefault("ELING_HOME", os.path.expanduser("~/.eling"))
os.makedirs(os.environ["ELING_HOME"], exist_ok=True)

# Change to a temp dir so CodeLayer auto-index doesn't scan home
import tempfile
tmpdir = tempfile.mkdtemp(prefix="as-brain-")
os.chdir(tmpdir)

from eling.as_brain.mcp_server import run_stdio
run_stdio()
''',
    }

    if dry_run:
        print(f"[dry-run] Scripts would be written to: {bin_dir}/")
        for name in scripts:
            print(f"  {name}")
        if getattr(args, "configure_zero", False):
            zero_cfg = Path(args.zero_config_dir or Path.home() / ".config" / "zero")
            print(f"  Zero config would be updated: {zero_cfg / 'config.json'}")
        print()
        return

    # Write scripts
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, content in scripts.items():
        path = bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  Written: {path}")

    # ── Optionally configure Zero ──
    if getattr(args, "configure_zero", False):
        import json as json_mod

        zero_cfg = Path(args.zero_config_dir or Path.home() / ".config" / "zero")
        cfg_path = zero_cfg / "config.json"
        if cfg_path.exists():
            cfg = json_mod.loads(cfg_path.read_text(encoding="utf-8"))
        else:
            cfg = {"mcp": {}}

        mcp = cfg.setdefault("mcp", {})
        mcp.setdefault("eling", {"command": str(bin_dir / "eling-termux-mcp"),
                                  "description": "Notion-based second brain (remote/online memory)"})
        mcp.setdefault("as_brain", {"command": str(bin_dir / "as-brain-mcp"),
                                     "description": "Local memory layers: facts, KB, code, builtin, HRR"})
        cfg["mcp"] = mcp

        zero_cfg.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json_mod.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated Zero MCP config: {cfg_path}")

    print("\n✅ Termux launcher scripts installed. Ensure ~/.local/bin is in your PATH.")
    if not getattr(args, "configure_zero", False):
        print("   Run with --configure-zero to update Zero's MCP config automatically.")


if __name__ == "__main__":
    main()

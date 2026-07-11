"""Eling configuration — layered fallback chain.

Priority (high → low):
  1. Hermes plugin config: `plugins.eling.*` in ~/.hermes/config.yaml
  2. Environment variables: `ELING_*`
  3. JSON config file: `~/.eling/config.json`
  4. Hardcoded defaults (below)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    "home": "",
    "hrr_dim": 512,
    "default_trust": 0.5,
    "min_trust": 0.0,
    "notion_enabled": True,
    "codegraph_enabled": True,
    "dedup_cache_size": 1000,
    "auto_sync_turns": True,
    "schema_pack": "default",
    "adapter": "hermes",
    "verify_on_stop": True,
    "verify_on_stop_max_attempts": 2,
}

ENV_MAP: dict[str, str] = {
    "home": "ELING_HOME",
    "hrr_dim": "ELING_HRR_DIM",
    "default_trust": "ELING_DEFAULT_TRUST",
    "min_trust": "ELING_MIN_TRUST",
    "notion_enabled": "ELING_NOTION_ENABLED",
    "codegraph_enabled": "ELING_CODEGRAPH_ENABLED",
    "dedup_cache_size": "ELING_DEDUP_CACHE_SIZE",
    "auto_sync_turns": "ELING_AUTO_SYNC_TURNS",
    "schema_pack": "ELING_SCHEMA_PACK",
    "adapter": "ELING_ADAPTER",
    "verify_on_stop": "ELING_VERIFY_ON_STOP",
    "verify_on_stop_max_attempts": "ELING_VERIFY_MAX_ATTEMPTS",
}

TYPE_MAP: dict[str, type] = {
    "hrr_dim": int,
    "default_trust": float,
    "min_trust": float,
    "notion_enabled": bool,
    "codegraph_enabled": bool,
    "dedup_cache_size": int,
    "auto_sync_turns": bool,
    "schema_pack": str,
    "adapter": str,
    "verify_on_stop": bool,
    "verify_on_stop_max_attempts": int,
}

# ── Schema packs ──────────────────────────────────────────────────────────────
# Each pack defines the category set available for its domain.
# 'default' is the baseline; additional packs extend or override.

SCHEMA_PACKS: dict[str, dict[str, Any]] = {
    "default": {
        "categories": [
            "general",
            "preference",
            "fact",
            "decision",
            "code",
        ],
    },
    "coding": {
        "categories": [
            "general",
            "preference",
            "fact",
            "decision",
            "code",
            "api_ref",
            "function",
            "bug_pattern",
            "config",
        ],
    },
    "research": {
        "categories": [
            "general",
            "preference",
            "fact",
            "decision",
            "source_note",
            "hypothesis",
            "finding",
            "method",
        ],
    },
}


def resolve_schema_pack(pack_name: str) -> dict[str, Any]:
    """Resolve a schema pack, merging with 'default' for the base."""
    from copy import deepcopy

    base = deepcopy(SCHEMA_PACKS.get("default", {}))
    if pack_name and pack_name != "default":
        overrides = SCHEMA_PACKS.get(pack_name, {})
        for key, vals in overrides.items():
            existing = base.get(key, [])
            base[key] = existing + [v for v in vals if v not in existing]
    return base


def categories_for_pack(pack_name: str) -> list[str]:
    """Return the list of valid categories for the given schema pack."""
    return resolve_schema_pack(pack_name).get("categories", [])


# ── Config file ops ───────────────────────────────────────────────────────────


def _config_path(home: str | None = None) -> Path:
    if home:
        return Path(home) / "config.json"
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "config.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ── Resolver ──────────────────────────────────────────────────────────────────


def _cast(key: str, value: Any) -> Any:
    """Cast a raw string to the expected type for this key."""
    if key not in TYPE_MAP:
        return value
    if value is None:
        return DEFAULTS.get(key)
    t = TYPE_MAP[key]
    if t is bool:
        if isinstance(value, bool):
            return value
        return (
            value.lower() in ("1", "true", "yes", "on")
            if isinstance(value, str)
            else bool(value)
        )
    return t(value)


def _resolve_env(key: str) -> str | None:
    env_name = ENV_MAP.get(key)
    return os.environ.get(env_name) if env_name else None


def _hermes_home() -> str:
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))


def resolve_config(hermes_config: dict | None = None) -> dict[str, Any]:
    """Resolve all config keys following the layered fallback chain.

    Priority (high → low):
      1. hermes_config (plugins.eling from ~/.hermes/config.yaml)
      2. env vars (ELING_*)
      3. config.json on disk (overrides via 'eling config set')
      4. hardcoded DEFAULTS
    """
    home_base = ""
    if hermes_config:
        home_base = hermes_config.get("home", "")
    if not home_base:
        try_env = _resolve_env("home")
        if try_env:
            home_base = try_env
    if not home_base:
        # Read config.json from the default location for the disk cache layer
        # This is the deepest fallback
        disk_path = _config_path()
        disk_cfg = _read_json(disk_path)
        home_base = disk_cfg.get("home", "")

    resolved = dict(DEFAULTS)

    # Layer 4: disk config file (if home is set by now, read from there)
    if home_base:
        disk_path = Path(home_base) / "config.json"
    else:
        disk_path = _config_path()
    if hermes_config:
        # Layer 1: Hermes plugin config
        for k, default in DEFAULTS.items():
            if k in hermes_config:
                resolved[k] = _cast(k, hermes_config[k])

    # Layer 2: env vars
    for k in DEFAULTS:
        env_val = _resolve_env(k)
        if env_val is not None:
            resolved[k] = _cast(k, env_val)

    # Layer 3: disk config.json
    disk_cfg = _read_json(disk_path)
    for k in DEFAULTS:
        if k in disk_cfg:
            # Only apply if not already set by a higher layer
            # env/hermes already handled above, but disk is next
            if k not in hermes_config if hermes_config else True:
                if _resolve_env(k) is None:
                    resolved[k] = _cast(k, disk_cfg[k])

    # Apply home
    resolved["home"] = home_base or _hermes_home() + "/eling-brain"

    # Ensure notion_enabled false when NOTION_API_KEY missing
    if resolved["notion_enabled"]:
        if not os.environ.get("NOTION_API_KEY"):
            resolved["notion_enabled"] = False

    return resolved


# ── User-facing CLI helpers ───────────────────────────────────────────────────


def get_config(home: str | None = None) -> dict:
    """Read the persistent config.json for the given home dir."""
    path = _config_path(home)
    return _read_json(path)


def set_config_key(key: str, value: Any, home: str | None = None) -> None:
    """Set a persistent config.json key. Validated against DEFAULTS."""
    if key not in DEFAULTS:
        raise ValueError(
            f"Unknown config key: {key}. Valid: {', '.join(sorted(DEFAULTS))}"
        )
    path = _config_path(home)
    data = _read_json(path)
    data[key] = _cast(key, value)
    _write_json(path, data)


def remove_config_key(key: str, home: str | None = None) -> None:
    """Remove a key from persistent config.json."""
    path = _config_path(home)
    data = _read_json(path)
    data.pop(key, None)
    _write_json(path, data)


def describe_config() -> dict[str, dict]:
    """Return schema description for all config keys."""
    return {
        "home": {
            "type": "string",
            "default": "",
            "env": "ELING_HOME",
            "description": "Data directory",
        },
        "hrr_dim": {
            "type": "int",
            "default": 512,
            "env": "ELING_HRR_DIM",
            "description": "HRR vector dimension (max 2048)",
        },
        "default_trust": {
            "type": "float",
            "default": 0.5,
            "env": "ELING_DEFAULT_TRUST",
            "description": "Default trust for new facts",
        },
        "min_trust": {
            "type": "float",
            "default": 0.0,
            "env": "ELING_MIN_TRUST",
            "description": "Minimum trust threshold for search",
        },
        "notion_enabled": {
            "type": "bool",
            "default": True,
            "env": "ELING_NOTION_ENABLED",
            "description": "Enable Notion layer",
        },
        "codegraph_enabled": {
            "type": "bool",
            "default": True,
            "env": "ELING_CODEGRAPH_ENABLED",
            "description": "Enable codegraph layer",
        },
        "dedup_cache_size": {
            "type": "int",
            "default": 1000,
            "env": "ELING_DEDUP_CACHE_SIZE",
            "description": "Dedup cache entry count",
        },
        "auto_sync_turns": {
            "type": "bool",
            "default": True,
            "env": "ELING_AUTO_SYNC_TURNS",
            "description": "Auto-store user/assistant messages",
        },
        "schema_pack": {
            "type": "str",
            "default": "default",
            "env": "ELING_SCHEMA_PACK",
            "description": "Category schema pack: default | coding | research",
        },
        "adapter": {
            "type": "str",
            "default": "hermes",
            "env": "ELING_ADAPTER",
            "description": "Harness adapter: hermes | claude_cli | opencode | openclaw | openclaude",
        },
        "verify_on_stop": {
            "type": "bool",
            "default": True,
            "env": "ELING_VERIFY_ON_STOP",
            "description": "Enable verify-on-stop nudges for non-Hermes agents",
        },
        "verify_on_stop_max_attempts": {
            "type": "int",
            "default": 2,
            "env": "ELING_VERIFY_MAX_ATTEMPTS",
            "description": "Max verification nudge retries per session",
        },
    }

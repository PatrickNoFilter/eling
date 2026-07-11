"""Permissions enforcement for multi-agent writes (Task 12.4).

Declarative access control: which source (agent identity) can read/write
which layer. Prevents cross-agent write contamination.

File format (JSON stored at ~/.eling/permissions.json):

    {
      "sources": {
        "hermes":  {"facts": "write", "kb": "write", "code": "write"},
        "claude":  {"facts": "write", "kb": "none",  "code": "read"},
        "opencode":{"facts": "read",  "kb": "none",  "code": "write"}
      }
    }

Access levels:
  - "write": can read AND write
  - "read":  can read (recall) only
  - "none":  no access at all

When the file is absent or a source/layer is not listed, full access is
granted (backward compatible).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ACCESS_LEVELS = ("write", "read", "none")
KNOWN_LAYERS = ("facts", "kb", "code", "notion", "hrr")


# ── File I/O ─────────────────────────────────────────────────────────


def _default_path() -> Path:
    """Resolve the default permissions file path."""
    eling_home = Path(
        os.environ.get("ELING_HOME", Path.home() / ".hermes" / "eling-brain")
    )
    return eling_home / "permissions.json"


def load_permissions(path: str | Path | None = None) -> dict[str, Any]:
    """Load permissions from a JSON file, returning an empty dict on failure."""
    p = Path(path) if path else _default_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


# ── Canonical permission ─────────────────────────────────────────────


def _canonical_access(raw: str) -> str:
    """Normalise an access string to one of the three known levels."""
    access = raw.strip().lower()
    return access if access in ACCESS_LEVELS else "write"


def check_access(
    source: str,
    layer: str,
    action: str = "write",
    perms: dict[str, Any] | None = None,
) -> bool:
    """Check whether *source* can perform *action* on *layer*.

    Parameters
    ----------
    source : str
        Agent identity (e.g. "hermes", "claude", "opencode").
    layer : str
        Layer name (e.g. "facts", "kb", "code", "notion", "hrr").
    action : str
        "read" or "write". Write implies read access.
    perms : dict or None
        Pre-loaded permissions dict. Loads from file if None.

    Returns
    -------
    bool
        True if access is allowed.
    """
    if perms is None:
        perms = load_permissions()

    sources = perms.get("sources", {})
    if not isinstance(sources, dict):
        return True  # invalid file → fall open

    source_cfg = sources.get(source)
    if source_cfg is None:
        return True  # unlisted source → full access

    if not isinstance(source_cfg, dict):
        return True

    access = source_cfg.get(layer, "write")
    access = _canonical_access(access)

    if access == "none":
        return False
    if action == "read":
        return True  # "write" or "read" both grant read
    if action == "write":
        return access == "write"

    return True


# ── Describe current state ───────────────────────────────────────────


def describe_permissions(perms: dict[str, Any] | None = None) -> list[dict]:
    """Return a human-readable list of all source → layer → access rules."""
    if perms is None:
        perms = load_permissions()
    sources = perms.get("sources", {})
    if not isinstance(sources, dict):
        return []

    rows: list[dict] = []
    for source, cfg in sorted(sources.items()):
        if not isinstance(cfg, dict):
            continue
        for layer in KNOWN_LAYERS:
            access = _canonical_access(cfg.get(layer, "write"))
            rows.append({"source": source, "layer": layer, "access": access})
    return rows

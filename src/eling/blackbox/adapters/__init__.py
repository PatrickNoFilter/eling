"""Shared utilities for telemetry adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def detect_project_key(cwd: str | None = None) -> str:
    """Detect the current project key (git repo basename or cwd)."""
    import os

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd or os.getcwd(),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except Exception:
        pass
    return Path(cwd or os.getcwd()).name if cwd else "unknown"


def detect_host_agent() -> str:
    """Detect which agent host is running (zero, hermes, etc.)."""
    import os

    if os.environ.get("HERMES_HOME") or os.environ.get("HERMES_CONFIG_PATH"):
        return "hermes"
    return "unknown"


def safe_truncate(text: str, max_chars: int = 200) -> str:
    """Truncate text safely for storage."""
    if not text:
        return ""
    return text[:max_chars] + "..." if len(text) > max_chars else text


def load_jsonl_lines(path: str | Path) -> list[dict[str, Any]]:
    """Load all JSON objects from a JSONL file."""
    import json

    lines = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return lines

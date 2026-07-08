#!/bin/sh
# Wrapper: run the Eling Continuum orchestration MCP server over stdio.
# Each AI agent (Hermes, OpenCode, MiMo-Code, Zero, Claude Code, Codex, Cline)
# points at this script so they all share ONE orchestration hub.
#
# Env:
#   ELING_HOME      shared eling base dir (continuum.db lives here). Point every
#                   agent at the SAME path so they share memory + orchestration.
#                   Default: ~/.eling
#   ELING_CONTINUUM_DB   explicit path to continuum.db (overrides ELING_HOME).
#   PYTHON          python3 to use (default: python3 on PATH).
set -e

# Resolve this script's location so it works from any install layout.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Prefer a venv next to the script (pip install -e . users), else system python.
if [ -x "$SCRIPT_DIR/../../../../bin/python" ]; then
    PY="$SCRIPT_DIR/../../../../bin/python"
elif [ -n "$PYTHON" ]; then
    PY="$PYTHON"
else
    PY="python3"
fi

# Auto-discover the eling package:
#   1. editable install -> importlib finds it
#   2. else run from the repo's src/ layout next to this file
PYTHONPATH="$SCRIPT_DIR/../..${PYTHONPATH:+:$PYTHONPATH}" \
exec "$PY" -m eling.continuum.mcp_server

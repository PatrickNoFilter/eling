#!/bin/sh
# Wrapper: run the Eling Markdownify MCP server over stdio.
# Converts documents (PDF, DOCX, XLSX, PPTX, images, audio, web pages) to Markdown.
# Uses Microsoft's markitdown Python library directly — no Node.js needed.
#
# Env:
#   MD_ALLOWED_PATHS   Colon-separated list of allowed directories for file reads.
#                      When set, all file-input tools reject paths outside these dirs.
#   MD_SHARE_DIR       Deprecated alias for a single allow-listed directory.
#   PYTHON             python3 to use (default: python3 on PATH).
set -e

# Resolve this script's location so it works from any install layout.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Prefer a venv next to the script, else system python.
if [ -x "$SCRIPT_DIR/../../../../bin/python" ]; then
    PY="$SCRIPT_DIR/../../../../bin/python"
elif [ -n "$PYTHON" ]; then
    PY="$PYTHON"
else
    PY="python3"
fi

# Auto-discover the eling package
PYTHONPATH="$SCRIPT_DIR/../..${PYTHONPATH:+:$PYTHONPATH}" \
exec "$PY" -m eling.markdownify.mcp_server

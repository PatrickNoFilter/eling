"""Markdownify MCP server — convert documents to Markdown.

Native Python implementation using Microsoft's markitdown library.
Follows the same MCP stdio pattern as eling's continuum, blackbox, and as_brain servers.

Tools:
  markdownify_pdf       — Convert a PDF file to Markdown
  markdownify_docx      — Convert a DOCX file to Markdown
  markdownify_xlsx      — Convert an XLSX file to Markdown
  markdownify_pptx      — Convert a PPTX file to Markdown
  markdownify_image     — Convert an image to Markdown (OCR + metadata)
  markdownify_audio     — Convert an audio file to Markdown with transcription
  markdownify_webpage   — Convert a web URL to Markdown
  markdownify_file      — Auto-detect file type and convert to Markdown
  markdownify_get       — Read an existing Markdown file

Environment:
  MD_ALLOWED_PATHS   Colon-separated list of allowed directories for file reads.
                      When set, all file-input tools reject paths outside these dirs.
                      Default: unrestricted.
  MD_SHARE_DIR       Deprecated alias (single directory, honoured for compatibility).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Lazy markitdown import ──────────────────────────────────────────────────

_md_instance = None


def _get_markitdown():
    """Lazy-import markitdown so the module loads even if markitdown isn't installed."""
    global _md_instance
    if _md_instance is None:
        try:
            from markitdown import MarkItDown

            _md_instance = MarkItDown()
        except ImportError:
            _md_instance = False  # sentinel
    if _md_instance is False:
        raise RuntimeError(
            "markitdown is not installed. Install with: pip install markitdown[all]"
        )
    return _md_instance


# ── Path restriction ─────────────────────────────────────────────────────────


def _resolve_allowed() -> list[Path] | None:
    """Parse MD_ALLOWED_PATHS / MD_SHARE_DIR into a list of allowed directories."""
    raw = os.environ.get("MD_ALLOWED_PATHS") or os.environ.get("MD_SHARE_DIR")
    if not raw:
        return None
    sep = ";" if os.name == "nt" else ":"
    return [Path(p).expanduser().resolve() for p in raw.split(sep) if p.strip()]


def _check_path(filepath: str, allowed: list[Path] | None) -> Path:
    """Validate and resolve a file path against allowed directories."""
    p = Path(filepath).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if allowed is not None:
        if not any(str(p).startswith(str(a)) for a in allowed):
            raise PermissionError(
                f"Access denied: {p} is not in allowed paths "
                f"({' : '.join(str(a) for a in allowed)})"
            )
    return p


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "markdownify_pdf",
        "description": "Convert a PDF file to Markdown text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the PDF file",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_docx",
        "description": "Convert a DOCX (Word) file to Markdown text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the DOCX file",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_xlsx",
        "description": "Convert an XLSX (Excel) file to Markdown text. "
        "Each worksheet is converted to a Markdown table.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the XLSX file",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_pptx",
        "description": "Convert a PPTX (PowerPoint) file to Markdown text. "
        "Extracts slide content, text, and notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the PPTX file",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_image",
        "description": "Convert an image file to Markdown with OCR-extracted text and metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the image file (PNG, JPG, etc.)",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_audio",
        "description": "Convert an audio file to Markdown with transcription. "
        "Requires markitdown[audio-transcription] or markitdown[all] installed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the audio file (MP3, WAV, etc.)",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_webpage",
        "description": "Convert a web page URL to Markdown text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the web page to convert",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "markdownify_file",
        "description": "Auto-detect file type and convert to Markdown. "
        "Supports: PDF, DOCX, XLSX, PPTX, images, audio, HTML, and more.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "markdownify_get",
        "description": "Retrieve the contents of an existing Markdown file "
        "(.md or .markdown extension required).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Absolute path to the Markdown file",
                },
            },
            "required": ["filepath"],
        },
    },
]

# ── Protocol handler ─────────────────────────────────────────────────────────


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
    except Exception as e:
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _handle_initialize(rid: int | str | None, params: dict) -> dict:
    client = params.get("clientInfo", {})
    logger.info(
        "Markdownify MCP client connected: %s %s",
        client.get("name", "?"),
        client.get("version", "?"),
    )
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eling-markdownify", "version": "0.1.0"},
        },
    }


def _handle_tool_call(rid: int | str | None, params: dict) -> dict:
    tool_name = params.get("name")
    args = dict(params.get("arguments", {}))
    allowed = _resolve_allowed()

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
        md = _get_markitdown()

        # ── File-based tools (all accept filepath) ──
        if tool_name in (
            "markdownify_pdf",
            "markdownify_docx",
            "markdownify_xlsx",
            "markdownify_pptx",
            "markdownify_image",
            "markdownify_audio",
            "markdownify_file",
        ):
            filepath = args.get("filepath", "")
            resolved = _check_path(filepath, allowed)
            result = md.convert(str(resolved))
            return ok(
                {
                    "file": str(resolved),
                    "format": "markdown",
                    "content": result.text_content,
                }
            )

        # ── Web page ──
        elif tool_name == "markdownify_webpage":
            url = args.get("url", "")
            if not url:
                return _error(rid, -32000, "url is required")
            result = md.convert_url(url)
            return ok(
                {
                    "url": url,
                    "format": "markdown",
                    "content": result.text_content,
                }
            )

        # ── Get markdown file ──
        elif tool_name == "markdownify_get":
            filepath = args.get("filepath", "")
            resolved = _check_path(filepath, allowed)
            ext = resolved.suffix.lower()
            if ext not in (".md", ".markdown"):
                return _error(
                    rid,
                    -32000,
                    f"File must have .md or .markdown extension, got: {ext}",
                )
            content = resolved.read_text(encoding="utf-8")
            return ok(
                {
                    "file": str(resolved),
                    "format": "markdown",
                    "content": content,
                }
            )

        else:
            return _error(rid, -32601, f"unknown tool: {tool_name}")

    except FileNotFoundError as e:
        return _error(rid, -32000, str(e))
    except PermissionError as e:
        return _error(rid, -32000, str(e))
    except ImportError as e:
        return _error(rid, -32000, str(e))
    except Exception as e:
        return _error(rid, -32000, f"{type(e).__name__}: {e}", traceback.format_exc())


def _error(
    rid: int | str | None, code: int, message: str, data: str | None = None
) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


# ── stdio entry ──────────────────────────────────────────────────────────────


def run_stdio() -> None:
    """Read JSON-RPC from stdin, write responses to stdout (MCP stdio transport)."""
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

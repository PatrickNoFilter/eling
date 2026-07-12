"""Markdownify Layer — document-to-Markdown conversion MCP server.

Adapts the functionality of github.com/zcaceres/markdownify-mcp into a native
Python MCP server using Microsoft's markitdown library. No Node.js, no Docker
subprocess — pure Python running inside eling's process model.

Provides tools for converting PDFs, DOCX, XLSX, PPTX, images, audio, and web
pages to clean Markdown text.
"""

from __future__ import annotations

from .mcp_server import run_stdio

__all__ = ["run_stdio"]

"""Notion layer — direct API client for online second brain.

Uses httpx (no MCP subprocess). Stores high-trust facts as Notion pages
under a parent page (Hermes Vault → Eling Brain).
"""

from __future__ import annotations

import logging
import os
import typing as _t

_HAS_HTTPX: bool | None = None  # lazy — checked on first use

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _require_httpx():
    """Import httpx lazily on first use. Returns the httpx module."""
    global _HAS_HTTPX
    import importlib

    try:
        mod = importlib.import_module("httpx")
        _HAS_HTTPX = True
        return mod
    except ImportError:
        _HAS_HTTPX = False
        raise RuntimeError("httpx not installed. Run: pip install eling-memory[notion]")


class NotionLayer:
    """Notion API client for online second brain."""

    def __init__(
        self,
        api_key: str | None = None,
        parent_page_id: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("NOTION_API_KEY")
        self.parent_page_id = parent_page_id or os.environ.get("NOTION_PARENT_PAGE_ID")
        self.timeout = timeout
        self._client: "_t.Any" = None  # httpx.Client when available

    @property
    def available(self) -> bool:
        global _HAS_HTTPX
        if _HAS_HTTPX is None:
            try:
                _require_httpx()
            except RuntimeError:
                _HAS_HTTPX = False
        return _HAS_HTTPX and bool(self.api_key)

    def _has_httpx(self) -> bool:
        global _HAS_HTTPX
        if _HAS_HTTPX is None:
            try:
                _require_httpx()
            except RuntimeError:
                _HAS_HTTPX = False
        return bool(_HAS_HTTPX)

    def _get_client(self) -> _t.Any:  # httpx.Client
        httpx = _require_httpx()
        if not self.api_key:
            raise RuntimeError("NOTION_API_KEY not set")
        if self._client is None:
            self._client = httpx.Client(
                base_url=NOTION_API,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search Notion pages by title. Returns slim {id, title, url}."""
        if not self.available:
            return []
        try:
            r = self._get_client().post(
                "/search",
                json={
                    "query": query,
                    "page_size": limit,
                    "filter": {"property": "object", "value": "page"},
                },
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for page in data.get("results", []):
                title = self._extract_title(page)
                results.append(
                    {
                        "id": page["id"],
                        "title": title,
                        "url": page.get("url", ""),
                        "last_edited": page.get("last_edited_time"),
                    }
                )
            return results
        except Exception as e:
            logger.warning("notion.search failed: %s", e)
            return []

    def get_page_markdown(self, page_id: str, prefer_full: bool = True) -> str:
        """Fetch page content as markdown.

        By default (``prefer_full=True``) uses the ``/v1/pages/<id>/markdown``
        endpoint, which returns FULL, un-truncated content (including complete
        API tokens). Notion's block API truncates secret values (e.g. tokens
        show only the last few chars), so the blocks walk is only used as a
        fallback when the markdown endpoint is unavailable.

        Set ``prefer_full=False`` to force the blocks walk (e.g. for non-secret
        KB indexing where truncation is acceptable and the endpoint is blocked).
        """
        if not self.available:
            return ""
        if prefer_full:
            full = self.get_page_full_markdown(page_id)
            if full:
                return full
            # fall through to blocks walk on empty/failed full retrieval
        try:
            blocks = self._fetch_block_children(page_id)
            return self._blocks_to_markdown(blocks)
        except Exception as e:
            logger.warning("notion.get_page failed: %s", e)
            return ""

    def get_page_full_markdown(self, page_id: str) -> str:
        """Fetch page content as markdown via the /v1/pages/<id>/markdown endpoint.

        Unlike `get_page_markdown` (which walks blocks and truncates secrets),
        this endpoint returns the FULL, un-truncated content — including complete
        API tokens. Use this when retrieving credential pages.
        """
        if not self.available:
            return ""
        try:
            client = self._get_client()
            # Use the newer markdown endpoint version for raw un-truncated text
            r = client.get(
                f"/pages/{page_id}/markdown",
                headers={"Notion-Version": "2025-09-03"},
            )
            r.raise_for_status()
            return r.json().get("markdown", "")
        except Exception as e:
            logger.warning("notion.get_page_full_markdown failed: %s", e)
            return ""

    def create_page(
        self, title: str, content: str, parent_id: str | None = None
    ) -> str | None:
        """Create a new page under parent. Returns new page_id or None."""
        if not self.available:
            return None
        parent = parent_id or self.parent_page_id
        if not parent:
            logger.warning("notion.create_page: no parent_page_id")
            return None
        try:
            blocks = self._markdown_to_blocks(content)
            r = self._get_client().post(
                "/pages",
                json={
                    "parent": {"page_id": parent},
                    "properties": {
                        "title": {"title": [{"text": {"content": title[:200]}}]}
                    },
                    "children": blocks[:100],  # Notion limit
                },
            )
            r.raise_for_status()
            return r.json()["id"]
        except Exception as e:
            logger.warning("notion.create_page failed: %s", e)
            return None

    def delete_page(self, page_id: str, hard: bool = False) -> bool:
        """Delete a Notion page.

        Notion has no true DELETE endpoint for pages — removal is done by
        archiving (PATCH .../pages/{id} with archived=true). When ``hard`` is
        True, the page's block children are removed first so that restoring
        the archived page yields an empty shell (cleaner removal of secrets).

        Returns True on success.
        """
        if not self.available:
            return False
        try:
            if hard:
                try:
                    children = self._fetch_block_children(page_id)
                    for blk in children:
                        bid = blk.get("id")
                        if not bid:
                            continue
                        self._get_client().delete(f"/blocks/{bid}")
                except Exception as e:  # non-fatal: proceed to archive anyway
                    logger.warning("notion.delete_page child purge failed: %s", e)
            r = self._get_client().patch(
                f"/pages/{page_id}",
                json={"archived": True},
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.warning("notion.delete_page failed: %s", e)
            return False

    def append_to_page(self, page_id: str, content: str) -> bool:
        """Append markdown content to existing page."""
        if not self.available:
            return False
        try:
            blocks = self._markdown_to_blocks(content)
            r = self._get_client().patch(
                f"/blocks/{page_id}/children",
                json={"children": blocks[:100]},
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.warning("notion.append failed: %s", e)
            return False

    def _fetch_block_children(self, block_id: str) -> list[dict]:
        out = []
        cursor = None
        client = self._get_client()
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            r = client.get(f"/blocks/{block_id}/children", params=params)
            r.raise_for_status()
            data = r.json()
            out.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return out

    @staticmethod
    def _extract_title(page: dict) -> str:
        props = page.get("properties", {})
        for key in ("title", "Name", "Title"):
            if key in props and props[key].get("title"):
                return "".join(t.get("plain_text", "") for t in props[key]["title"])
        return "(untitled)"

    @staticmethod
    def _blocks_to_markdown(blocks: list[dict]) -> str:
        lines = []
        for b in blocks:
            t = b.get("type")
            data = b.get(t, {}) if t else {}
            rich = data.get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            if t == "paragraph":
                lines.append(text)
            elif t == "heading_1":
                lines.append(f"# {text}")
            elif t == "heading_2":
                lines.append(f"## {text}")
            elif t == "heading_3":
                lines.append(f"### {text}")
            elif t == "bulleted_list_item":
                lines.append(f"- {text}")
            elif t == "numbered_list_item":
                lines.append(f"1. {text}")
            elif t == "code":
                lang = data.get("language", "")
                lines.append(f"```{lang}\n{text}\n```")
            elif t == "quote":
                lines.append(f"> {text}")
            else:
                if text:
                    lines.append(text)
        return "\n\n".join(lines)

    @staticmethod
    def _markdown_to_blocks(content: str) -> list[dict]:
        """Simple markdown → Notion blocks converter."""
        blocks = []
        for line in content.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("# "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {
                            "rich_text": [{"text": {"content": line[2:][:2000]}}]
                        },
                    }
                )
            elif line.startswith("## "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"text": {"content": line[3:][:2000]}}]
                        },
                    }
                )
            elif line.startswith("### "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {
                            "rich_text": [{"text": {"content": line[4:][:2000]}}]
                        },
                    }
                )
            elif line.startswith("- "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"text": {"content": line[2:][:2000]}}]
                        },
                    }
                )
            elif line.startswith("> "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "quote",
                        "quote": {
                            "rich_text": [{"text": {"content": line[2:][:2000]}}]
                        },
                    }
                )
            else:
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"text": {"content": line[:2000]}}]
                        },
                    }
                )
        return blocks

    def close(self):
        if self._client:
            self._client.close()

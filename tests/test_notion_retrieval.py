"""Tests for Notion retrieval fallback behavior.

The block walk endpoint truncates secret values (returns [REDACTED]),
so the default get_page_markdown must prefer the full-markdown endpoint
and fall back to blocks only when the markdown endpoint fails.
"""

from unittest.mock import MagicMock


from eling.layers.notion import NotionLayer


def _make_layer():
    layer = NotionLayer(api_key="test-key")
    layer._client = MagicMock()
    return layer


def test_get_page_markdown_prefers_full():
    layer = _make_layer()
    layer._client.get.return_value.json.return_value = {
        "markdown": "```\nghp_fulltokenvalue\n```"
    }
    layer._client.get.return_value.raise_for_status.return_value = None
    out = layer.get_page_markdown("page-id")
    # full-markdown endpoint returns the un-truncated token, not [REDACTED]
    assert "ghp_fulltokenvalue" in out
    assert "[REDACTED]" not in out
    # markdown endpoint called, not blocks children
    assert layer._client.get.called
    assert "/markdown" in layer._client.get.call_args[0][0]


def test_get_page_markdown_falls_back_to_blocks():
    layer = _make_layer()
    # markdown endpoint raises -> fall back to block children walk
    full = MagicMock()
    full.raise_for_status.side_effect = Exception("no markdown endpoint")
    blocks = MagicMock()
    blocks.raise_for_status.return_value = None
    blocks.json.return_value = {
        "results": [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": "secret"}]},
            }
        ],
        "has_more": False,
    }
    layer._client.get.side_effect = [full, blocks]
    out = layer.get_page_markdown("page-id")
    assert "secret" in out


def test_get_page_markdown_prefer_full_false_uses_blocks():
    layer = _make_layer()
    blocks = MagicMock()
    blocks.raise_for_status.return_value = None
    blocks.json.return_value = {
        "results": [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "x"}]}}
        ],
        "has_more": False,
    }
    layer._client.get.return_value = blocks
    out = layer.get_page_markdown("page-id", prefer_full=False)
    assert out == "x"
    # markdown endpoint must NOT have been called
    for call in layer._client.get.call_args_list:
        assert "/markdown" not in call[0][0]


def test_get_page_full_markdown_handles_missing_key():
    layer = _make_layer()
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {}  # no "markdown" key
    layer._client.get.return_value = resp
    assert layer.get_page_full_markdown("page-id") == ""

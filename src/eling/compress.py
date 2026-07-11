"""Optional LLM compression for Eling memory ingestion.

When LLM_COMPRESS=true, uses a configured LLM endpoint to compress content
before storage. Falls back to simple truncation when not configured.

Pipeline position: after privacy filter, before indexing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Default: no LLM, use length-based truncation
_LLM_COMPRESS = os.environ.get("ELING_LLM_COMPRESS", "").lower() in ("1", "true", "yes")
_COMPRESS_MAX_CHARS = int(os.environ.get("ELING_COMPRESS_MAX_CHARS", "2000"))
_COMPRESS_MIN_CHARS = int(os.environ.get("ELING_COMPRESS_MIN_CHARS", "100"))
_LLM_ENDPOINT = os.environ.get("ELING_LLM_ENDPOINT", "")
_LLM_API_KEY = os.environ.get("ELING_LLM_API_KEY", "")


def compress(content: str) -> str:
    """Compress content for storage.

    If LLM compression is enabled and configured, delegates to llm_compress().
    Otherwise, truncates long content with a summary suffix.

    Content under _COMPRESS_MIN_CHARS is returned as-is.
    """
    if not content or len(content) < _COMPRESS_MIN_CHARS:
        return content

    if _LLM_COMPRESS and _LLM_ENDPOINT:
        return _llm_compress(content)

    # Default: simple length-based compression
    return _truncate_compress(content)


def _truncate_compress(content: str) -> str:
    """Simple length-based compression with structural preservation.

    Strategy:
    - Keep first 3 lines (usually heading/context)
    - Keep last 2 lines (usually conclusion)
    - For middle: keep any lines with markdown headings, code blocks, or list items
    - Summarize the rest with line count
    """
    if len(content) <= _COMPRESS_MAX_CHARS:
        return content

    lines = content.split("\n")
    if len(lines) <= 10:
        # Short but over char limit — keep first/last
        head = "\n".join(lines[:3])
        tail = "\n".join(lines[-2:])
        return f"{head}\n\n[... {len(lines) - 5} lines compressed ...]\n\n{tail}"

    # Longer content: keep headings, code fences, and list items
    important: list[str] = []
    skipped = 0
    in_code = False

    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_code = not in_code
            important.append(line)
        elif in_code or line.startswith(("# ", "## ", "### ", "- ", "* ", "1. ")):
            important.append(line)
        elif i < 3 or i >= len(lines) - 2:
            important.append(line)
        else:
            skipped += 1

    result = "\n".join(important)
    if skipped > 0:
        result += f"\n\n[... {skipped} lines compressed ...]"

    # If still over limit, just truncate
    if len(result) > _COMPRESS_MAX_CHARS * 1.5:
        result = result[:_COMPRESS_MAX_CHARS] + "\n\n[... truncated ...]"

    return result


def _llm_compress(content: str) -> str:
    """Compress via configured LLM endpoint.

    Uses a simple POST request to an OpenAI-compatible chat endpoint.
    Falls back to truncation on any error.
    """
    try:
        import httpx

        prompt = (
            "Compress the following content for an AI agent's memory. "
            "Keep all factual information, entities, relationships, and key insights. "
            "Remove redundancy, fluff, and conversational filler. "
            f"Target: under {_COMPRESS_MAX_CHARS} characters.\n\n---\n{content}\n---"
        )

        resp = httpx.post(
            _LLM_ENDPOINT + "/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a compression assistant. Return only the compressed text, no explanation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.1,
            },
            headers={
                "Authorization": f"Bearer {_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        compressed = data["choices"][0]["message"]["content"].strip()
        logger.info(
            "llm_compress: %d → %d chars (-%d%%)",
            len(content),
            len(compressed),
            (1 - len(compressed) / len(content)) * 100 if content else 0,
        )
        return compressed
    except Exception as e:
        logger.warning("llm_compress failed, fallback to truncation: %s", e)
        return _truncate_compress(content)


def configure(
    *,
    enabled: bool | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    max_chars: int | None = None,
) -> None:
    """Override compression settings at runtime."""
    global _LLM_COMPRESS, _LLM_ENDPOINT, _LLM_API_KEY, _COMPRESS_MAX_CHARS
    if enabled is not None:
        _LLM_COMPRESS = enabled
    if endpoint is not None:
        _LLM_ENDPOINT = endpoint
    if api_key is not None:
        _LLM_API_KEY = api_key
    if max_chars is not None:
        _COMPRESS_MAX_CHARS = max_chars

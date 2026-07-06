"""Eling — unified second brain for AI agents.

5-layer architecture: builtin / facts / kb / code / notion
Features: HRR reasoning, gap analysis, Notion auto-sync, verify-on-stop.

MCP split:
  - `eling.mcp_server` → notion-only MCP server
  - `eling.as_brain.mcp_server` → local layers MCP server (facts, KB, code, builtin, HRR)
"""

__version__ = "0.7.3"
__all__ = [
    "Brain", "HookRegistry", "ALL_HOOKS", "register_default_hooks",
    "remember", "recall", "reason", "resolve_config", "set_config_key",
    "get_config", "describe_config",
    "verify_on_stop", "detect_host_agent", "host_has_verify_on_stop",
    "FactMemoryProvider",
]

from .brain import Brain
from .hooks import HookRegistry, ALL_HOOKS, register_default_hooks
from .config import resolve_config, set_config_key, get_config, describe_config
from . import verify_on_stop
from .fact_memory_provider import FactMemoryProvider

_default_brain: Brain | None = None


def _get_default() -> Brain:
    global _default_brain
    if _default_brain is None:
        _default_brain = Brain()
    return _default_brain


def remember(content: str, **kwargs) -> dict:
    """Quick-access remember on default brain."""
    return _get_default().remember(content, **kwargs)


def recall(query: str, **kwargs) -> dict:
    """Quick-access recall on default brain."""
    return _get_default().recall(query, **kwargs)


def reason(entities: list[str], **kwargs) -> list[dict]:
    """Quick-access reason on default brain."""
    return _get_default().reason(entities, **kwargs)

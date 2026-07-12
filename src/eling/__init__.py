"""Eling — unified second brain for AI agents.

5-layer architecture: builtin / facts / kb / code / notion
Features: HRR reasoning, gap analysis, Notion auto-sync, verify-on-stop.

MCP modules:
  - `eling.mcp_server` → notion-only MCP server
  - `eling.as_brain.mcp_server` → local layers MCP server (facts, KB, code, builtin, HRR)
  - `eling.blackbox.mcp_server` → flight recorder / telemetry
  - `eling.continuum.mcp_server` → multi-agent orchestration hub
  - `eling.markdownify.mcp_server` → document-to-Markdown conversion (markitdown)
"""

__version__ = "0.12.0"
__all__ = [
    "Brain",
    "HookRegistry",
    "ALL_HOOKS",
    "register_default_hooks",
    "remember",
    "recall",
    "reason",
    "resolve_config",
    "set_config_key",
    "get_config",
    "describe_config",
    "verify_on_stop",
    "detect_host_agent",
    "host_has_verify_on_stop",
    "FactMemoryProvider",
    "BlackboxStore",
    "EfficiencyScorer",
    "EffectivenessScorer",
]

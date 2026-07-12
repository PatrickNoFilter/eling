"""Eling Blackbox — Layer 2: flight recorder for coding agents.

Observes agent actions (reads, edits, commands, delegations, test outcomes),
scores context efficiency, measures effectiveness, and stores telemetry
for cross-session analysis.

Architecture:
  Layer 2 sits above Builtin (Layer 1) and below Facts (Layer 3).
  Events flow: Blackbox captures → Facts stores findings as HRR vectors →
  KB archives efficiency history → Notion backs up periodic summaries →
  Continuum (Layer 7) orchestrates agents that consume all lower layers.

Supports:
  - Zero (stream-JSON protocol via telemetry plugin)
  - Hermes (session DB tap)
  - Generic MCP-based agents
"""

__version__ = "0.1.0"

__all__ = [
    "BlackboxStore",
    "EfficiencyScorer",
    "EffectivenessScorer",
    "CausalTimeline",
    "ZeroAdapter",
    "HermesAdapter",
    "run_cli",
]

from .store import BlackboxStore
from .score import EfficiencyScorer
from .effectiveness import EffectivenessScorer
from .timeline import CausalTimeline
from .adapters.zero import ZeroAdapter
from .adapters.hermes import HermesAdapter
from .cli import run_cli

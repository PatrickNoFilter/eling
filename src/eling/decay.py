"""Forgetting/decay engine — exponential strength decay with 3-state lifecycle.

Provides the decay function, lifecycle classification, and constants used by
FactsLayer and the idle_30min hook (Task 12.1).
"""

from __future__ import annotations

import math

DEFAULT_DECAY_RATE = 0.1
"""Default λ: 0.1/day → ~6.9 day half-life for unused facts."""

ACTIVE_THRESHOLD = 0.5
"""Strengths > this are 'active' (returned in default recall)."""

DORMANT_THRESHOLD = 0.2
"""Strengths > this but <= ACTIVE_THRESHOLD are 'dormant' (hidden from default recall)."""

CLEARED_RECOVERY_DAYS = 30
"""Facts in 'cleared' state are soft-deleted but recoverable for this many days."""

READ_BOOST = 0.05
"""Strength boost applied on search/recall hit."""

DECISION_BOOST = 0.10
"""Strength boost applied on decision_made / helpful feedback."""


def decay_strength(
    strength: float,
    days: float,
    decay_rate: float = DEFAULT_DECAY_RATE,
) -> float:
    """Apply exponential decay: strength *= exp(-decay_rate * days).

    Result is clamped to [0.0, 1.0].
    """
    s = strength * math.exp(-decay_rate * days)
    return max(0.0, min(1.0, s))


def compute_lifecycle(strength: float) -> str:
    """Classify a strength value into a lifecycle state.

    Returns one of:
        - "active"  (strength > ACTIVE_THRESHOLD)
        - "dormant" (DORMANT_THRESHOLD <= strength <= ACTIVE_THRESHOLD)
        - "cleared" (strength < DORMANT_THRESHOLD)
    """
    if strength > ACTIVE_THRESHOLD:
        return "active"
    if strength >= DORMANT_THRESHOLD:
        return "dormant"
    return "cleared"


def boost_strength(
    strength: float,
    boost: float = READ_BOOST,
) -> float:
    """Add a boost to strength, clamped to [0.0, 1.0]."""
    return max(0.0, min(1.0, strength + boost))

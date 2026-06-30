"""Eling — unified second brain for AI agents.

5-layer architecture: builtin / facts / kb / code / notion
"""

__version__ = "0.1.0"
__all__ = ["Brain", "remember", "recall", "reason"]

from .brain import Brain

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

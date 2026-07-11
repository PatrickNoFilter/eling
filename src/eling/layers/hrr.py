"""Holographic Reduced Representations (HRR) with phase encoding.

Adapted from the holographic memory plugin by dusterbloom (Hermes PR #2351, MIT).
Standalone — no Hermes coupling.

HRRs are a vector symbolic architecture for encoding compositional structure
into fixed-width distributed representations. Phase vectors: each concept is
a vector of angles in [0, 2π). Operations:

  bind   — circular convolution (phase addition)
  unbind — circular correlation (phase subtraction)
  bundle — superposition (circular mean)

References:
  Plate (1995) — Holographic Reduced Representations
  Gayler (2004) — Vector Symbolic Architectures
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct

_HAS_NUMPY: bool | None = None  # lazy — checked on first use

logger = logging.getLogger(__name__)
_TWO_PI = 2.0 * math.pi


def _require_numpy():
    """Ensures numpy is available; imports on first call.

    Returns the ``numpy`` module so callers can use the returned reference
    instead of a module-level import.
    """
    global _HAS_NUMPY
    import importlib

    try:
        mod = importlib.import_module("numpy")
        _HAS_NUMPY = True
        return mod
    except ImportError:
        _HAS_NUMPY = False
        raise RuntimeError(
            "numpy is required for HRR operations. Install with: pip install eling-memory[hrr]"
        )


def encode_atom(word: str, dim: int = 1024):
    """Deterministic phase vector via SHA-256 counter blocks."""
    np = _require_numpy()
    values_per_block = 16
    blocks_needed = math.ceil(dim / values_per_block)
    uint16_values: list[int] = []
    for i in range(blocks_needed):
        digest = hashlib.sha256(f"{word}:{i}".encode()).digest()
        uint16_values.extend(struct.unpack("<16H", digest))
    return np.array(uint16_values[:dim], dtype=np.float64) * (_TWO_PI / 65536.0)


def bind(a, b):
    """Circular convolution = element-wise phase addition."""
    _require_numpy()
    return (a + b) % _TWO_PI


def unbind(memory, key):
    """Circular correlation = element-wise phase subtraction."""
    _require_numpy()
    return (memory - key) % _TWO_PI


def bundle(*vectors):
    """Superposition via circular mean of complex exponentials."""
    np = _require_numpy()
    complex_sum = np.sum([np.exp(1j * v) for v in vectors], axis=0)
    return np.angle(complex_sum) % _TWO_PI


def similarity(a, b) -> float:
    """Phase cosine similarity. Range [-1, 1]."""
    np = _require_numpy()
    return float(np.mean(np.cos(a - b)))


def encode_text(text: str, dim: int = 1024):
    """Bag-of-words: bundle of atom vectors for each token."""
    _require_numpy()  # ensure numpy available even if bundle/encode_atom lazy
    tokens = [t.strip(".,!?;:\"'()[]{}") for t in text.lower().split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return encode_atom("__hrr_empty__", dim)
    return bundle(*[encode_atom(t, dim) for t in tokens])


def encode_fact(content: str, entities: list[str], dim: int = 1024):
    """Structured encoding: content + entities bound to role atoms."""
    _require_numpy()
    role_content = encode_atom("__hrr_role_content__", dim)
    role_entity = encode_atom("__hrr_role_entity__", dim)
    components = [bind(encode_text(content, dim), role_content)]
    for entity in entities:
        components.append(bind(encode_atom(entity.lower(), dim), role_entity))
    return bundle(*components)


def phases_to_bytes(phases) -> bytes:
    """Serialize phase vector to bytes (float64, 8KB at dim=1024)."""
    _require_numpy()
    return phases.tobytes()


def bytes_to_phases(data: bytes):
    """Deserialize bytes back to phase vector."""
    np = _require_numpy()
    return np.frombuffer(data, dtype=np.float64).copy()


def snr_estimate(dim: int, n_items: int) -> float:
    """Signal-to-noise ratio for holographic storage. SNR < 2.0 = degraded."""
    if n_items <= 0:
        return float("inf")
    snr = math.sqrt(dim / n_items)
    if snr < 2.0:
        logger.warning(
            "HRR storage near capacity: SNR=%.2f (dim=%d, n_items=%d)",
            snr,
            dim,
            n_items,
        )
    return snr

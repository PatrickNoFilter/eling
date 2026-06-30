"""Tests for eling.layers.hrr — HRR phase encoding."""

import math
import pytest

pytest.importorskip("numpy")
import numpy as np

from eling.layers import hrr


class TestEncodeAtom:
    def test_deterministic(self):
        v1 = hrr.encode_atom("hello", dim=128)
        v2 = hrr.encode_atom("hello", dim=128)
        assert np.allclose(v1, v2)

    def test_shape(self):
        v = hrr.encode_atom("test", dim=256)
        assert v.shape == (256,)
        assert v.dtype == np.float64

    def test_in_range(self):
        v = hrr.encode_atom("any_word", dim=512)
        assert v.min() >= 0.0
        assert v.max() < 2 * math.pi

    def test_different_words_differ(self):
        v1 = hrr.encode_atom("cat", dim=512)
        v2 = hrr.encode_atom("dog", dim=512)
        sim = hrr.similarity(v1, v2)
        # Random atoms should be near-orthogonal: |sim| < 0.3
        assert abs(sim) < 0.3


class TestBindUnbind:
    def test_bind_unbind_roundtrip(self):
        a = hrr.encode_atom("subject", dim=1024)
        b = hrr.encode_atom("object", dim=1024)
        bound = hrr.bind(a, b)
        recovered = hrr.unbind(bound, a)
        # unbind(bind(a,b), a) ≈ b
        sim = hrr.similarity(recovered, b)
        assert sim > 0.95

    def test_bind_dissimilar_to_inputs(self):
        a = hrr.encode_atom("apple", dim=1024)
        b = hrr.encode_atom("banana", dim=1024)
        bound = hrr.bind(a, b)
        # Bound vector should be dissimilar to both inputs (quasi-orthogonal)
        assert abs(hrr.similarity(bound, a)) < 0.3
        assert abs(hrr.similarity(bound, b)) < 0.3


class TestBundle:
    def test_bundle_preserves_similarity(self):
        a = hrr.encode_atom("alpha", dim=1024)
        b = hrr.encode_atom("beta", dim=1024)
        c = hrr.encode_atom("gamma", dim=1024)
        bundled = hrr.bundle(a, b, c)
        # Bundled vector should be more similar to each input than random
        for v in (a, b, c):
            assert hrr.similarity(bundled, v) > 0.3

    def test_bundle_single(self):
        a = hrr.encode_atom("only", dim=512)
        bundled = hrr.bundle(a)
        # Bundling single vector returns near-identical
        assert hrr.similarity(bundled, a) > 0.95


class TestSimilarity:
    def test_identical_vectors(self):
        v = hrr.encode_atom("same", dim=512)
        assert hrr.similarity(v, v) == pytest.approx(1.0)

    def test_range(self):
        a = hrr.encode_atom("a", dim=512)
        b = hrr.encode_atom("b", dim=512)
        s = hrr.similarity(a, b)
        assert -1.0 <= s <= 1.0


class TestEncodeText:
    def test_handles_empty(self):
        v = hrr.encode_text("", dim=256)
        assert v.shape == (256,)

    def test_punctuation_stripped(self):
        v1 = hrr.encode_text("hello world", dim=512)
        v2 = hrr.encode_text("hello, world!", dim=512)
        # Should be identical after punctuation stripping
        assert hrr.similarity(v1, v2) > 0.95

    def test_case_insensitive(self):
        v1 = hrr.encode_text("Hello World", dim=512)
        v2 = hrr.encode_text("hello world", dim=512)
        assert hrr.similarity(v1, v2) > 0.95


class TestEncodeFact:
    def test_with_entities(self):
        v = hrr.encode_fact("Patrick uses Python", ["Patrick", "Python"], dim=1024)
        assert v.shape == (1024,)

    def test_no_entities(self):
        v = hrr.encode_fact("Just some content", [], dim=512)
        assert v.shape == (512,)


class TestSerialization:
    def test_roundtrip(self):
        v = hrr.encode_atom("serialize_me", dim=1024)
        bytes_data = hrr.phases_to_bytes(v)
        v_restored = hrr.bytes_to_phases(bytes_data)
        assert np.array_equal(v, v_restored)

    def test_byte_size(self):
        v = hrr.encode_atom("test", dim=1024)
        bytes_data = hrr.phases_to_bytes(v)
        # float64 → 8 bytes per element
        assert len(bytes_data) == 1024 * 8


class TestSNREstimate:
    def test_empty_returns_inf(self):
        assert hrr.snr_estimate(1024, 0) == float("inf")

    def test_typical(self):
        snr = hrr.snr_estimate(1024, 16)
        assert snr == pytest.approx(8.0, rel=0.01)

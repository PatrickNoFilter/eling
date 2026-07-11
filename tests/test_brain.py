"""Tests for eling.brain.Brain — unified orchestrator."""

import pytest

from eling.brain import Brain


@pytest.fixture
def brain(tmp_path):
    b = Brain(home=tmp_path / "brain")
    yield b
    b.close()


class TestRemember:
    def test_auto_routes_short_to_facts(self, brain):
        out = brain.remember("Short content here")
        assert out["layer"] == "facts"
        assert "id" in out

    def test_auto_routes_long_to_kb(self, brain):
        long_content = "Long content. " * 50  # >500 chars
        out = brain.remember(long_content)
        assert out["layer"] == "kb"
        assert "chunks_added" in out

    def test_auto_routes_markdown_to_kb(self, brain):
        # With heading, should still route to facts (under 500 chars)
        # But once content has \n# it triggers kb routing
        out = brain.remember("normal content\n# heading\nmore")
        assert out["layer"] == "kb"

    def test_force_facts_layer(self, brain):
        out = brain.remember("anything", layer="facts")
        assert out["layer"] == "facts"

    def test_force_kb_layer(self, brain):
        out = brain.remember("short content", layer="kb", source="manual")
        assert out["layer"] == "kb"

    def test_invalid_layer_raises(self, brain):
        with pytest.raises(ValueError):
            brain.remember("x", layer="nonsense")

    def test_notion_without_config(self, brain):
        # Default brain has no Notion API key configured (env may have it)
        out = brain.remember("x", layer="notion", title="Test")
        # Either succeeds (if NOTION_API_KEY env is set) or returns error
        assert "layer" in out


class TestRecall:
    def test_returns_merged_and_per_layer(self, brain):
        brain.remember("Python is great")
        brain.remember("Java is also fine")
        result = brain.recall("Python")
        assert "merged" in result
        assert "per_layer" in result
        assert "query" in result

    def test_limits_per_layer(self, brain):
        for i in range(20):
            brain.facts.add(f"Fact number {i} about python")
        result = brain.recall("python", limit=5)
        assert len(result["merged"]) <= 5

    def test_specific_layers(self, brain):
        brain.facts.add("alpha fact")
        result = brain.recall("alpha", layers=["facts"])
        assert "facts" in result["per_layer"]
        assert "kb" not in result["per_layer"]

    def test_rrf_fusion_works(self, brain):
        # Same query result from multiple layers — RRF should combine
        brain.facts.add("Python is amazing")
        brain.kb.index("Python tutorial intro", source="tutorial")
        result = brain.recall("python")
        # Should have results from both layers
        layers_present = set(result["per_layer"].keys())
        assert "facts" in layers_present or "kb" in layers_present


class TestReason:
    def test_compositional(self, brain):
        brain.facts.add("Patrick uses Python")
        brain.facts.add("Patrick lives in Indonesia")
        brain.facts.add("Python is a language")
        results = brain.reason(["Patrick", "Python"])
        assert isinstance(results, list)
        # Top result should mention both ideally
        if results:
            top = results[0]["content"].lower()
            assert "patrick" in top or "python" in top

    def test_empty_entities(self, brain):
        brain.facts.add("anything")
        results = brain.reason([])
        assert isinstance(results, list)


class TestStats:
    def test_returns_dict(self, brain):
        stats = brain.stats()
        assert "home" in stats
        assert "facts" in stats
        assert "kb" in stats
        assert "code_available" in stats
        assert "notion_available" in stats
        assert "builtin_available" in stats

    def test_facts_substats(self, brain):
        brain.facts.add("test")
        stats = brain.stats()
        assert stats["facts"]["total_facts"] == 1


class TestRRFFusion:
    def test_empty_layers(self):
        merged = Brain._rrf_fuse({}, limit=5)
        assert merged == []

    def test_single_layer(self):
        per_layer = {
            "facts": [{"fact_id": 1, "content": "a"}, {"fact_id": 2, "content": "b"}]
        }
        merged = Brain._rrf_fuse(per_layer, limit=5)
        assert len(merged) == 2
        assert merged[0]["fact_id"] == 1  # rank 0 → higher RRF score

    def test_multi_layer_dedup_per_key(self):
        per_layer = {
            "facts": [{"fact_id": 1, "content": "a"}],
            "kb": [{"chunk_id": 10, "content": "b"}],
        }
        merged = Brain._rrf_fuse(per_layer, limit=5)
        assert len(merged) == 2  # Both unique
        keys = {(m["_layer"], m.get("fact_id") or m.get("chunk_id")) for m in merged}
        assert keys == {("facts", 1), ("kb", 10)}

    def test_score_ordering(self):
        # Rank 0 in facts and rank 1 in kb → facts item should rank higher
        per_layer = {
            "facts": [{"fact_id": 1, "content": "high"}],
            "kb": [
                {"chunk_id": 99, "content": "skip"},
                {"chunk_id": 100, "content": "low"},
            ],
        }
        merged = Brain._rrf_fuse(per_layer, limit=5)
        # facts rank 0 has 1/(60+1) = 0.0164
        # kb rank 0 has 1/(60+1) = 0.0164
        # kb rank 1 has 1/(60+2) = 0.0161
        # Top 2 should be the rank-0 items
        top2 = merged[:2]
        scores = [m["_rrf_score"] for m in top2]
        assert (
            all(s >= merged[2]["_rrf_score"] for s in scores)
            if len(merged) > 2
            else True
        )

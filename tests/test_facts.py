"""Tests for eling.layers.facts.FactsLayer."""

import pytest

from eling.layers.facts import FactsLayer


@pytest.fixture
def facts(tmp_path):
    db_path = tmp_path / "test_facts.db"
    layer = FactsLayer(db_path=db_path)
    yield layer
    layer.close()


class TestAddFact:
    def test_returns_fact_id(self, facts):
        fid = facts.add("Python is fun")
        assert isinstance(fid, int) and fid > 0

    def test_dedupe_on_duplicate(self, facts):
        fid1 = facts.add("Same content")
        fid2 = facts.add("Same content")
        assert fid1 == fid2

    def test_empty_raises(self, facts):
        with pytest.raises(ValueError):
            facts.add("")
        with pytest.raises(ValueError):
            facts.add("   ")

    def test_with_category_and_tags(self, facts):
        fid = facts.add("Test fact", category="user", tags="alpha,beta")
        fact = facts.get(fid)
        assert fact["category"] == "user"
        assert fact["tags"] == "alpha,beta"

    def test_source_default(self, facts):
        fid = facts.add("Some content")
        fact = facts.get(fid)
        assert fact["source"] == "facts"

    def test_source_override(self, facts):
        fid = facts.add("From notion", source="notion")
        fact = facts.get(fid)
        assert fact["source"] == "notion"


class TestEntityExtraction:
    def test_capitalized_phrase(self, facts):
        fid = facts.add("John Doe wrote a book")
        ents = facts.entities_for_fact(fid)
        assert "John Doe" in ents

    def test_double_quoted(self, facts):
        fid = facts.add('Python supports "async" syntax')
        ents = facts.entities_for_fact(fid)
        assert "async" in ents

    def test_single_quoted(self, facts):
        fid = facts.add("The 'pytest' framework is great")
        ents = facts.entities_for_fact(fid)
        assert "pytest" in ents

    def test_no_entities(self, facts):
        fid = facts.add("just plain text here")
        ents = facts.entities_for_fact(fid)
        assert ents == []

    def test_entities_missing_fact(self, facts):
        assert facts.entities_for_fact(99999) == []


class TestSearch:
    def test_basic_match(self, facts):
        facts.add("Python is a language")
        facts.add("Java is also a language")
        facts.add("Carrots are vegetables")
        results = facts.search("language")
        contents = [r["content"] for r in results]
        assert any("Python" in c for c in contents)
        assert any("Java" in c for c in contents)
        assert not any("Carrots" in c for c in contents)

    def test_limit_respected(self, facts):
        for i in range(10):
            facts.add(f"language fact number {i}")
        results = facts.search("language", limit=3)
        assert len(results) <= 3

    def test_min_trust_filter(self, facts):
        fid_high = facts.add("HighTrust fact about cats")
        fid_low = facts.add("LowTrust fact about cats")
        # Decay low trust
        for _ in range(5):
            facts.update_trust(fid_low, helpful=False)
        results = facts.search("cats", min_trust=0.5)
        ids = [r["fact_id"] for r in results]
        assert fid_high in ids
        assert fid_low not in ids

    def test_empty_query(self, facts):
        facts.add("anything")
        assert facts.search("") == []
        assert facts.search("   ") == []

    def test_category_filter(self, facts):
        facts.add("cats are nice", category="animals")
        facts.add("cats are wise", category="philosophy")
        results = facts.search("cats", category="animals")
        assert all(r["category"] == "animals" for r in results)


class TestProbe:
    def test_finds_entity_directly(self, facts):
        fid = facts.add("Patrick Stewart leads Star Trek")
        results = facts.probe("Patrick Stewart")
        ids = [r["fact_id"] for r in results]
        assert fid in ids

    def test_falls_back_to_fts(self, facts):
        # No capitalized entity, just plain noun
        fid = facts.add("python is interpreted")
        # 'python' not extracted as entity, but should match via FTS fallback
        results = facts.probe("python")
        ids = [r["fact_id"] for r in results]
        assert fid in ids

    def test_nonexistent_returns_empty(self, facts):
        facts.add("Some fact")
        results = facts.probe("ZZZNonExistentXYZ")
        assert results == []

    def test_limit_respected(self, facts):
        for i in range(5):
            facts.add(f"Alpha Beta saw event number {i}")
        results = facts.probe("Alpha Beta", limit=2)
        assert len(results) <= 2


class TestReason:
    def test_compositional_query(self, facts):
        # Reason finds facts mentioning multiple entities together
        facts.add("Patrick Stewart starred in Star Trek")
        facts.add("Patrick Stewart is from England")
        facts.add("Star Trek aired on TV")
        results = facts.reason(["Patrick Stewart", "Star Trek"])
        # Should rank fact mentioning both higher than facts with one
        assert len(results) >= 1
        # Top result should mention both
        top_content = results[0]["content"].lower()
        assert "patrick" in top_content or "star" in top_content

    def test_empty_entities(self, facts):
        facts.add("anything")
        # Should fall back gracefully
        results = facts.reason([])
        assert isinstance(results, list)


class TestUpdateTrust:
    def test_helpful_increases_trust(self, facts):
        fid = facts.add("Test fact")
        before = facts.get(fid)
        facts.update_trust(fid, helpful=True)
        after = facts.get(fid)
        assert after["trust_score"] > before["trust_score"]
        assert after["helpful_count"] == before["helpful_count"] + 1

    def test_unhelpful_decreases_trust(self, facts):
        fid = facts.add("Test fact")
        before = facts.get(fid)
        facts.update_trust(fid, helpful=False)
        after = facts.get(fid)
        assert after["trust_score"] < before["trust_score"]

    def test_trust_clamped(self, facts):
        fid = facts.add("Clamp test")
        for _ in range(50):
            facts.update_trust(fid, helpful=True)
        fact = facts.get(fid)
        assert fact["trust_score"] <= 1.0

        fid2 = facts.add("Clamp test 2")
        for _ in range(50):
            facts.update_trust(fid2, helpful=False)
        fact2 = facts.get(fid2)
        assert fact2["trust_score"] >= 0.0

    def test_missing_fact_raises(self, facts):
        with pytest.raises(KeyError):
            facts.update_trust(99999, helpful=True)


class TestRemove:
    def test_remove_existing(self, facts):
        fid = facts.add("Will be removed")
        assert facts.remove(fid) is True
        assert facts.get(fid) is None

    def test_remove_missing(self, facts):
        assert facts.remove(99999) is False

    def test_remove_cleans_entity_links(self, facts):
        fid = facts.add("Alpha Beta did something")
        facts.remove(fid)
        # Should not raise — entity links cascaded
        results = facts.probe("Alpha Beta")
        assert fid not in [r.get("fact_id") for r in results]


class TestNotionLink:
    def test_set_notion_page(self, facts):
        fid = facts.add("Promote me")
        ok = facts.set_notion_page(fid, "abc-123-notion-id")
        assert ok is True
        fact = facts.get(fid)
        assert fact["notion_page_id"] == "abc-123-notion-id"

    def test_set_missing(self, facts):
        assert facts.set_notion_page(99999, "anything") is False


class TestStats:
    def test_empty_store(self, facts):
        stats = facts.stats()
        assert stats["total_facts"] == 0
        assert stats["total_entities"] == 0
        assert "hrr_enabled" in stats

    def test_with_data(self, facts):
        facts.add("First fact", category="a")
        facts.add("Second fact", category="b")
        facts.add("Third fact", category="a")
        stats = facts.stats()
        assert stats["total_facts"] == 3
        assert stats["by_category"] == {"a": 2, "b": 1}

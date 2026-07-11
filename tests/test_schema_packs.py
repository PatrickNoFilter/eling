"""Tests for schema packs (Task 12.7)."""

from __future__ import annotations


from eling.config import (
    SCHEMA_PACKS,
    resolve_schema_pack,
    categories_for_pack,
    DEFAULTS,
)


class TestSchemaPackDefaults:
    def test_default_pack_has_categories(self):
        pack = resolve_schema_pack("default")
        cats = pack["categories"]
        assert "general" in cats
        assert "preference" in cats
        assert "fact" in cats
        assert "decision" in cats
        assert "code" in cats

    def test_default_pack_size(self):
        cats = categories_for_pack("default")
        assert len(cats) == 5

    def test_empty_pack(self):
        """Unknown pack falls back to default categories."""
        cats = categories_for_pack("nope")
        assert cats == categories_for_pack("default")


class TestCodingPack:
    def test_coding_has_default_plus_extra(self):
        cats = categories_for_pack("coding")
        # All default categories present
        for c in ("general", "preference", "fact", "decision", "code"):
            assert c in cats
        # Coding-specific categories
        for c in ("api_ref", "function", "bug_pattern", "config"):
            assert c in cats

    def test_coding_larger_than_default(self):
        default = len(categories_for_pack("default"))
        coding = len(categories_for_pack("coding"))
        assert coding > default


class TestResearchPack:
    def test_research_has_default_plus_extra(self):
        cats = categories_for_pack("research")
        for c in ("general", "preference", "fact", "decision", "code"):
            assert c in cats
        for c in ("source_note", "hypothesis", "finding", "method"):
            assert c in cats


class TestConfigDefaults:
    def test_schema_pack_default_in_dot_defaults(self):
        assert DEFAULTS["schema_pack"] == "default"

    def test_adapter_default_in_dot_defaults(self):
        assert DEFAULTS["adapter"] == "hermes"


class TestResolveSchemaPack:
    def test_resolve_does_not_mutate_original(self):
        """resolve_schema_pack returns a copy, not mutating SCHEMA_PACKS."""
        original_len = len(SCHEMA_PACKS["default"]["categories"])
        resolve_schema_pack("coding")
        assert len(SCHEMA_PACKS["default"]["categories"]) == original_len

    def test_categories_for_pack_no_duplicates(self):
        """Even if a pack repeats a default category, no duplicates."""
        cats = categories_for_pack("coding")
        assert len(cats) == len(set(cats))

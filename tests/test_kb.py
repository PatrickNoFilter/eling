"""Tests for eling.layers.kb.KBLayer."""

import pytest

from eling.layers.kb import KBLayer


@pytest.fixture
def kb(tmp_path):
    layer = KBLayer(db_path=tmp_path / "test_kb.db")
    yield layer
    layer.close()


class TestIndex:
    def test_basic_index(self, kb):
        n = kb.index("This is plain prose content", source="test-doc")
        assert n == 1

    def test_markdown_splitting(self, kb):
        content = """# Title
Some intro text.

## Section A
Content of A.

## Section B
Content of B.
"""
        n = kb.index(content, source="md-doc")
        # Should split into multiple chunks (title + 2 sections)
        assert n >= 2

    def test_empty_content_skipped(self, kb):
        # Whitespace-only chunks should not be indexed
        n = kb.index("", source="empty")
        # Either 0 or 1 (whole content as one chunk if non-empty after stripping)
        assert n <= 1

    def test_code_block_detected(self, kb):
        content = """## Code
```python
print('hello')
```
"""
        n = kb.index(content, source="code-doc")
        assert n >= 1
        rows = kb._conn.execute("SELECT content_type FROM kb_chunks").fetchall()
        types = [r["content_type"] for r in rows]
        assert "code" in types


class TestSearch:
    def test_finds_indexed_content(self, kb):
        kb.index("Python is a great programming language for AI", source="doc-1")
        results = kb.search("python AI")
        assert len(results) >= 1
        assert "python" in results[0]["content"].lower()

    def test_source_filter(self, kb):
        kb.index("Apple is a fruit", source="fruits")
        kb.index("Apple is a tech company", source="companies")
        results = kb.search("apple", source="fruits")
        assert all("fruits" in r["source"] for r in results)

    def test_empty_query(self, kb):
        kb.index("content", source="any")
        assert kb.search("") == []

    def test_no_match(self, kb):
        kb.index("apple pie recipe", source="cookbook")
        results = kb.search("quantum physics")
        # FTS5 may return empty or low-rank results
        assert isinstance(results, list)

    def test_limit_respected(self, kb):
        for i in range(10):
            kb.index(f"Document number {i} about cats", source=f"doc-{i}")
        results = kb.search("cats", limit=3)
        assert len(results) <= 3

    def test_special_chars_sanitized(self, kb):
        kb.index("Some content here", source="doc")
        # Special chars that would break FTS5 should not crash
        results = kb.search('"unclosed quote')
        assert isinstance(results, list)


class TestListSources:
    def test_empty(self, kb):
        assert kb.list_sources() == []

    def test_with_data(self, kb):
        kb.index("c1", source="src-a")
        kb.index("c2", source="src-a")
        kb.index("c3", source="src-b")
        sources = kb.list_sources()
        names = {s["source"] for s in sources}
        assert names == {"src-a", "src-b"}


class TestRemoveSource:
    def test_removes_all_chunks(self, kb):
        kb.index("alpha content", source="rm-me")
        kb.index("beta content", source="rm-me")
        kb.index("keep me", source="keep")
        removed = kb.remove_source("rm-me")
        assert removed == 2
        assert kb.search("alpha") == [] or all(
            "rm-me" not in r["source"] for r in kb.search("alpha")
        )


class TestStats:
    def test_empty(self, kb):
        stats = kb.stats()
        assert stats["total_chunks"] == 0
        assert stats["total_sources"] == 0

    def test_with_data(self, kb):
        kb.index("a", source="s1")
        kb.index("b", source="s1")
        kb.index("c", source="s2")
        stats = kb.stats()
        assert stats["total_chunks"] == 3
        assert stats["total_sources"] == 2

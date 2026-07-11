"""Tests for Eling compression pipeline."""

from eling.compress import compress, configure


class TestTruncateCompress:
    def test_short_content_returned_as_is(self):
        text = "Short text"
        assert compress(text) == text

    def test_content_under_min_chars(self):
        text = "x" * 50
        assert compress(text) == text

    def test_long_content_truncated_preserving_headings(self):
        lines = [
            "# Big Heading",
            "Some intro paragraph here.",
            "",
            "## Section 1",
            "Detailed content... " * 100,
            "",
            "## Section 2",
            "More content. " * 100,
            "",
            "## Conclusion",
            "Final remarks.",
        ]
        text = "\n".join(lines)
        result = compress(text)
        assert "# Big Heading" in result
        assert "## Section 1" in result
        assert "## Section 2" in result
        assert "## Conclusion" in result
        assert "[... " in result or "[... truncated ...]" in result
        assert len(result) < len(text)

    def test_short_but_over_limit_preserves_first_last(self):
        lines = ["Line A", "Line B", "Line C", "Line D", "Line E", "Line F"]
        # Configure a very small max_chars so it triggers
        configure(max_chars=10)
        try:
            text = "\n".join(lines)
            result = compress(text)
            assert "Line A" in result
            assert "Line F" in result
        finally:
            configure(max_chars=2000)  # restore

    def test_empty_content(self):
        assert compress("") == ""

    def test_content_with_code_blocks(self):
        text = """# Code Example

Some description.

```python
def hello():
    print("world")
```

End.
"""
        result = compress(text)
        assert "def hello():" in result
        assert "```" in result

    def test_content_with_list_items(self):
        text = """# Features

- Feature one: does something
- Feature two: does another thing
  - Nested: detail
- Feature three

That's it.
"""
        result = compress(text)
        assert "- Feature one" in result
        assert "- Feature three" in result or "- Feature two" in result


class TestCompressPassthrough:
    def test_content_under_min_chars_passes(self):
        from eling.compress import _COMPRESS_MIN_CHARS

        assert _COMPRESS_MIN_CHARS > 0
        text = "x" * (_COMPRESS_MIN_CHARS - 1)
        assert compress(text) == text

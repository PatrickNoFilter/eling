"""Tests for Eling builtin layer."""

import os
import tempfile
from pathlib import Path


from eling.layers.builtin import BuiltinLayer


class TestBuiltinLayer:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memory_file = self.tmp / "MEMORY.md"
        self.user_file = self.tmp / "USER.md"
        # Clear env to avoid polluting tests
        os.environ.pop("HERMES_HOME", None)

    def test_init_uses_explicit_paths(self):
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        assert bl.memory_path == self.memory_file
        assert bl.user_path == self.user_file

    def test_init_defaults(self):
        # Will use ~/.hermes/MEMORY.md etc but not be available
        bl = BuiltinLayer()
        assert "MEMORY" in str(bl.memory_path)
        assert "USER" in str(bl.user_path)

    def test_available_false_when_missing(self):
        bl = BuiltinLayer(
            memory_path=self.tmp / "nonexistent.md", user_path=self.tmp / "nouser.md"
        )
        assert not bl.available

    def test_available_true_when_memory_exists(self):
        self.memory_file.write_text("# Test memory")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        assert bl.available

    def test_available_true_when_user_exists(self):
        self.user_file.write_text("# Test user")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        assert bl.available

    def test_read_memory_returns_content(self):
        self.memory_file.write_text("Important: user prefers short responses")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        content = bl.read_memory()
        assert "short responses" in content

    def test_read_memory_missing_returns_empty(self):
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        assert bl.read_memory() == ""

    def test_read_user_returns_content(self):
        self.user_file.write_text("Name: Test User")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        content = bl.read_user()
        assert "Test User" in content

    def test_read_user_missing_returns_empty(self):
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        assert bl.read_user() == ""

    def test_search_matches_line(self):
        self.memory_file.write_text("line 1\nprefers short responses\nline 3")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        results = bl.search("short")
        assert len(results) >= 1
        assert results[0]["line"] == 2
        assert results[0]["source"] == "memory"

    def test_search_no_match(self):
        self.memory_file.write_text("line 1\nline 2")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        results = bl.search("nonexistent")
        assert results == []

    def test_search_both_files(self):
        self.memory_file.write_text("neural network topology\nsomething else")
        self.user_file.write_text("neural network enthusiast")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        results = bl.search("neural")
        assert len(results) >= 2

    def test_search_case_insensitive(self):
        self.memory_file.write_text("Hello World")
        bl = BuiltinLayer(memory_path=self.memory_file, user_path=self.user_file)
        results = bl.search("hello")
        assert len(results) == 1
        assert results[0]["content"] == "Hello World"

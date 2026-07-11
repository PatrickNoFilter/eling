"""Tests for Eling CLI commands."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _eling(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `python -m eling.cli` with given args, return completed process.

    Each subprocess creates a new Brain (SQLite init ≈ 5-8s), so use a
    generous timeout.
    """
    result = subprocess.run(
        [sys.executable, "-m", "eling.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            k: v
            for k, v in os.environ.items()
            if k not in ("ELING_HOME", "NOTION_API_KEY")
        },
    )
    return result


class TestCLIStats:
    def test_stats_returns_json(self):
        result = _eling("stats")
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert "facts" in data
        assert "kb" in data
        assert "hooks" in data

    def test_stats_facts_count(self):
        result = _eling("stats")
        import json

        data = json.loads(result.stdout)
        assert isinstance(data["facts"]["total_facts"], int)


class TestCLIRemember:
    def test_remember_facts(self):
        text = f"CLI test fact {os.urandom(4).hex()}"
        result = _eling("remember", text)
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert data.get("fact_id", data.get("id", 0)) > 0

    def test_remember_kb(self):
        result = _eling("remember", "CLI kb test content", "--layer", "kb")
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert data.get("chunks_added", 0) >= 1

    def test_remember_invalid_layer(self):
        result = _eling("remember", "test", "--layer", "invalid")
        assert result.returncode != 0


class TestCLIRecall:
    def test_recall_returns_list(self):
        _eling("remember", "pineapple pizza is great", "--category", "test")
        result = _eling("recall", "pineapple")
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert "merged" in data
        assert "per_layer" in data

    def test_recall_limit(self):
        result = _eling("recall", "test", "--limit", "3")
        import json

        json.loads(result.stdout)
        assert result.returncode == 0


class TestCLIReason:
    def test_reason_returns_list(self):
        result = _eling("reason", "Eling", "Memory")
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestCLIReflect:
    def test_reflect_nonexistent_fact(self):
        result = _eling("reflect", "999999")
        # Should not crash; return error dict
        import json

        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert "error" in data or "notion" in data


class TestCLIConfig:
    def test_config_get_hrr_dim(self):
        result = _eling("config", "get", "hrr_dim")
        assert result.returncode == 0
        assert "512" in result.stdout

    def test_config_get_home(self):
        result = _eling("config", "get", "home")
        assert result.returncode == 0
        assert result.stdout.strip()

    def test_config_schema(self):
        result = _eling("config", "schema")
        assert result.returncode == 0
        import json

        schema = json.loads(result.stdout)
        assert "hrr_dim" in schema
        assert "home" in schema
        assert "notion_enabled" in schema

    def test_config_set_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = tmp
            _eling("config", "set", "hrr_dim", "256", "--home", home)
            result = _eling("config", "get", "hrr_dim")
            assert result.returncode == 0

    def test_config_init_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _eling("config", "init", "--home", tmp)
            assert result.returncode == 0
            assert Path(tmp, "config.json").exists()

    def test_config_ls(self):
        result = _eling("config", "ls")
        assert result.returncode == 0
        assert "KEY" in result.stdout
        assert "VALUE" in result.stdout


class TestCLISync:
    def test_sync_flush(self):
        result = _eling("sync", "--direction", "flush", "--once")
        assert result.returncode == 0
        import json

        data = json.loads(result.stdout)
        assert data["layers"]["facts_flushed"] is True
        assert data["layers"]["kb_flushed"] is True


class TestCLIHelp:
    def test_help_default(self):
        result = _eling("--help")
        assert result.returncode == 0
        assert "usage:" in result.stdout or "Usage:" in result.stdout

    def test_subcommand_help(self):
        result = _eling("sync", "--help")
        assert result.returncode == 0
        assert "--direction" in result.stdout


class TestCLIErrors:
    def test_invalid_command(self):
        result = _eling("nonexistent_command_xyz")
        assert result.returncode != 0

    def test_no_subcommand(self):
        result = subprocess.run(
            [sys.executable, "-m", "eling.cli"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0

"""Tests for Eling configuration system."""

import os
import pytest
from eling import config as cfg


class TestDefaults:
    def test_defaults_keys(self):
        assert "home" in cfg.DEFAULTS
        assert "hrr_dim" in cfg.DEFAULTS
        assert cfg.DEFAULTS["hrr_dim"] == 512

    def test_env_map_complete(self):
        for k in cfg.DEFAULTS:
            assert k in cfg.ENV_MAP, f"{k} missing from ENV_MAP"


class TestConfigResolution:
    def test_resolve_returns_all_keys(self):
        resolved = cfg.resolve_config()
        for k in cfg.DEFAULTS:
            assert k in resolved, f"{k} missing from resolved"

    def test_hermes_config_overrides_default(self):
        resolved = cfg.resolve_config({"hrr_dim": 256})
        assert resolved["hrr_dim"] == 256

    def test_env_overrides_default(self):
        os.environ["ELING_HRR_DIM"] = "128"
        try:
            resolved = cfg.resolve_config()
            assert resolved["hrr_dim"] == 128
        finally:
            del os.environ["ELING_HRR_DIM"]

    def test_env_overrides_hermes_config(self):
        os.environ["ELING_HRR_DIM"] = "64"
        try:
            resolved = cfg.resolve_config({"hrr_dim": 256})
            assert resolved["hrr_dim"] == 64
        finally:
            del os.environ["ELING_HRR_DIM"]

    def test_notion_disabled_when_key_missing(self):
        os.environ.pop("NOTION_API_KEY", None)
        resolved = cfg.resolve_config({"notion_enabled": True})
        assert resolved["notion_enabled"] is False

    def test_min_trust_cast(self):
        os.environ["ELING_MIN_TRUST"] = "0.3"
        try:
            resolved = cfg.resolve_config()
            assert resolved["min_trust"] == 0.3
        finally:
            del os.environ["ELING_MIN_TRUST"]

    def test_bool_cast_from_env(self):
        os.environ["ELING_CODEGRAPH_ENABLED"] = "false"
        try:
            resolved = cfg.resolve_config()
            assert resolved["codegraph_enabled"] is False
        finally:
            del os.environ["ELING_CODEGRAPH_ENABLED"]

    def test_home_fallback(self):
        resolved = cfg.resolve_config()
        assert resolved["home"]  # should be a non-empty string

    def test_none_hermes_config(self):
        resolved = cfg.resolve_config(None)
        assert "hrr_dim" in resolved


class TestGetSet:
    def test_set_remove_roundtrip(self, tmp_path):
        cfg.set_config_key("hrr_dim", 1024, home=str(tmp_path))
        data = cfg.get_config(home=str(tmp_path))
        assert data.get("hrr_dim") == 1024

        cfg.remove_config_key("hrr_dim", home=str(tmp_path))
        data = cfg.get_config(home=str(tmp_path))
        assert "hrr_dim" not in data

    def test_invalid_key_raises(self):
        with pytest.raises(ValueError, match="Unknown config key"):
            cfg.set_config_key("nope", 42)


class TestDescribe:
    def test_describe_includes_all_keys(self):
        desc = cfg.describe_config()
        for k in cfg.DEFAULTS:
            assert k in desc
            assert "type" in desc[k]
            assert "description" in desc[k]
            assert "env" in desc[k]

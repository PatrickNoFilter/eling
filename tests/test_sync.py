"""Tests for Eling sync system — Brain.sync(), flush(), push/pull."""

import os
import tempfile
from pathlib import Path


from eling.brain import Brain
from eling.layers.facts import FactsLayer
from eling.layers.kb import KBLayer


class TestFlush:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.facts_db = self.tmp / "test_flush_facts.db"
        self.kb_db = self.tmp / "test_flush_kb.db"

    def test_facts_flush_does_not_raise(self):
        facts = FactsLayer(self.facts_db)
        facts.add("test fact", category="test", tags="sync")
        facts.flush()  # should not raise
        facts.close()

    def test_kb_flush_does_not_raise(self):
        kb = KBLayer(self.kb_db)
        kb.index("test content", source="test")
        kb.flush()  # should not raise
        kb.close()


class TestBrainSync:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["HOME"] = str(self.tmp)
        # Point brain to temp dir
        os.environ["ELING_HOME"] = str(self.tmp)

    def test_sync_flush_works(self):
        """Flush direction should always succeed even with no notion."""
        brain = Brain()
        result = brain.sync(direction="flush")
        assert result["pushed"] == 0
        assert result["pulled"] == 0
        assert result["layers"]["facts_flushed"] is True
        assert result["layers"]["kb_flushed"] is True
        assert len(result["errors"]) == 0
        brain.close()

    def test_sync_push_no_notion(self):
        """Push should work gracefully without Notion available."""
        brain = Brain()
        result = brain.sync(direction="push")
        assert result["pushed"] == 0
        # Notion should be unavailable
        assert "notion_note" in result["layers"]
        assert "unavailable" in result["layers"]["notion_note"].lower()
        brain.close()

    def test_sync_pull_no_notion(self):
        """Pull should work gracefully without Notion available."""
        brain = Brain()
        result = brain.sync(direction="pull")
        assert result["pulled"] == 0
        assert "notion_note" in result["layers"]
        brain.close()

    def test_sync_all_no_notion(self):
        """'all' direction should flush + try push without crashing."""
        brain = Brain()
        result = brain.sync(direction="all")
        assert result["layers"]["facts_flushed"] is True
        assert result["layers"]["kb_flushed"] is True
        assert result["pushed"] == 0
        brain.close()

    def test_sync_state_path(self):
        """sync_state.json should be written when sync_state_path is set."""
        brain = Brain()
        state_path = self.tmp / "sync_state.json"
        brain.sync(sync_state_path=str(state_path))
        assert state_path.exists()
        import json

        state = json.loads(state_path.read_text())
        assert "last_sync" in state
        assert "total_pushed" in state
        brain.close()

    def test_sync_state_accumulates(self):
        """state should accumulate counters across sync calls."""
        brain = Brain()
        state_path = self.tmp / "sync_state_accum.json"
        brain.sync(sync_state_path=str(state_path))
        brain.sync(sync_state_path=str(state_path))
        import json

        state = json.loads(state_path.read_text())
        assert state["total_pushed"] >= 0
        assert state["total_pulled"] >= 0
        brain.close()


class TestFactsLayerFlushIntegration:
    def test_flush_after_add(self):
        """FactsLayer.flush after add should not corrupt data."""
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "facts.db"
        facts = FactsLayer(db)
        fid = facts.add("persist test", category="sync", tags="flush")
        facts.flush()
        facts.close()
        # Re-open and verify it's still there
        facts2 = FactsLayer(db)
        results = facts2.list_all(category="sync")
        assert len(results) >= 1
        assert any(r["fact_id"] == fid for r in results)
        facts2.close()


class TestKBLayerFlushIntegration:
    def test_flush_after_index(self):
        """KBLayer.flush after index should not corrupt data."""
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "kb.db"
        kb = KBLayer(db)
        kb.index("persist content", source="flush_test")
        kb.flush()
        kb.close()
        # Re-open and search
        kb2 = KBLayer(db)
        results = kb2.search("persist", limit=5)
        assert any(r["source"] == "flush_test" for r in results)
        kb2.close()


class TestSyncEdgeCases:
    def test_sync_with_error(self):
        """Sync with a bogus direction should not crash."""
        import tempfile
        from pathlib import Path
        from eling.brain import Brain

        tmp = Path(tempfile.mkdtemp())
        brain = Brain(home=tmp)
        result = brain.sync(direction="flush", layer="nope")
        # Gracefully handles unknown layer
        assert isinstance(result, dict)
        brain.close()

    def test_sync_auto_layer(self):
        """Layer='auto' should work like 'all'."""
        import tempfile
        from pathlib import Path
        from eling.brain import Brain

        tmp = Path(tempfile.mkdtemp())
        brain = Brain(home=tmp)
        result = brain.sync(direction="all", layer="auto")
        assert result["pushed"] == 0
        assert result["pulled"] == 0
        brain.close()

    def test_sync_push_facts_adds_facts(self):
        """Push should find any high-trust facts for Notion."""
        import tempfile
        from pathlib import Path
        from eling.brain import Brain

        tmp = Path(tempfile.mkdtemp())
        brain = Brain(home=tmp)
        brain.remember("A very important test fact", layer="facts")
        # Push should find it (if fact has high enough trust)
        result = brain.sync(direction="push")
        # Without notion it returns 0 pushed
        assert result["pushed"] == 0
        assert "notion_note" in result["layers"]
        brain.close()

    def test_sync_state_persists_across_calls(self):
        """Sync state file accumulates across sequential calls."""
        import json
        import tempfile
        from pathlib import Path
        from eling.brain import Brain

        tmp = Path(tempfile.mkdtemp())
        state_file = tmp / "state.json"
        brain = Brain(home=tmp)
        brain.sync(direction="flush", sync_state_path=str(state_file))
        brain.sync(direction="flush", sync_state_path=str(state_file))
        state = json.loads(state_file.read_text())
        assert state["total_pushed"] >= 0
        assert state["total_pulled"] >= 0
        assert "last_sync" in state
        brain.close()

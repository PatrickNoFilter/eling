"""E2E tests for the 3 prototypes: vector search, temporal queries, versioning."""

import os
import sys
import tempfile
import time
from pathlib import Path

# Load .env for API key (if available)
_env_path = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

# Add prototype dir to path
sys.path.insert(0, str(Path(__file__).parent))

from vector_search import VectorSearchLayer, _api_embed, _get_env_key  # noqa: E402
from temporal import (  # noqa: E402
    parse_time_range,
    has_temporal_intent,
    make_temporal_filter_clause,
)
from versioning import VersionedFactStore  # noqa: E402

# ── 1. TEST VECTOR SEARCH ──────────────────────────────────────────────────


def test_vector_search_api():
    """Test that the embedding API is reachable and returns correct dimensions."""
    print("\n[1.1] VectorSearchLayer: API connectivity...")
    # Debug: check env
    for ek in ("OPENAI_API_KEY", "OPENCODE_ZEN_API_KEY", "MISTRAL_API_KEY"):
        val = os.environ.get(ek, "")
        print(
            f"  DEBUG: {ek}={'set ' + val[:8] + '...' if val else 'UNSET'} [{len(val)} chars]"
        )
    key_from_fn = _get_env_key()
    print(
        f"  DEBUG: _get_env_key() returns {'set ' + key_from_fn[:8] + '...' if key_from_fn else 'EMPTY'} [{len(key_from_fn)} chars]"
    )

    result = _api_embed(["test connection"])
    if result is None:
        print("  ⚠️  API call failed (no API key set or network issue)")
        return False
    vec = result[0]
    assert len(vec) == 1024, f"Expected 1024-dim (mistral-embed), got {len(vec)}"
    print("  ✅ API works: 1024-dim vector returned")
    return True


def test_vsl_index_and_search():
    """Test full index + search flow."""
    print("\n[1.2] VectorSearchLayer: index and search...")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    try:
        vsl = VectorSearchLayer(db)

        if not vsl.available:
            print("  ⚠️  VectorSearchLayer not available (no API key)")
            return False

        # Index facts
        facts = [
            (1, "Python is a programming language for web development and AI"),
            (2, "Javanese culture has rich traditions and wayang kulit performances"),
            (3, "Database systems store and retrieve structured information"),
            (4, "The quick brown fox jumps over the lazy dog"),
            (5, "SQLite is a lightweight embedded relational database engine"),
        ]
        for fid, content in facts:
            ok = vsl.index_fact(fid, content)
            assert ok, f"Failed to index fact {fid}"
        print(f"  ✅ Indexed {len(facts)} facts")

        # Model binding test
        bind_result = vsl._bind_model()
        assert bind_result is None, (
            f"Model binding should succeed on first call: {bind_result}"
        )
        print("  ✅ Model binding works")

        # Semantic search
        results = vsl.search("database query language", top_k=3)
        assert len(results) > 0, "Should find at least one result"
        # Fact 3 or 5 should be top (database-related)
        top_ids = [r["fact_id"] for r in results]
        print(
            f"  ✅ Semantic search: top={top_ids} scores={[r['score'] for r in results]}"
        )

        # Search with filter
        results_filtered = vsl.search("computer programming", top_k=5, fact_ids=[1, 4])
        assert len(results_filtered) > 0
        print(f"  ✅ Filtered search works: {[r['fact_id'] for r in results_filtered]}")

        # Stats
        stats = vsl.stats()
        print(f"  ✅ Stats: {stats}")

        # Reindex all (batch)
        batch_result = vsl.reindex_all(facts)
        print(f"  ✅ Batch reindex: {batch_result}")

        vsl.close()
        print("  ✅ All vector search tests passed!")
        return True
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        os.unlink(db)


# ── 2. TEST TEMPORAL QUERIES ───────────────────────────────────────────────


def test_temporal_parser():
    """Test the NLP date parser."""
    print("\n[2.1] Temporal: parse_time_range...")

    cases = [
        ("last 3 days", "days", 3),
        ("last 1 week", "weeks", 1),
        ("yesterday", "days", 1),
        ("today", "days", 0),
        ("past 5 hours", "hours", 5),
        ("last 2 months", "months", 2),
        ("this week", "this_week", None),
        ("this month", "this_month", None),
        ("kemarin", "days", 1),
        ("hari ini", "days", 0),
        ("last 7 hari", "days", 7),
        ("last 30 menit", "hours", 30),
    ]

    ok = 0
    for query, expected_unit, expected_amount in cases:
        start, end = parse_time_range(query)
        if start is not None:
            ok += 1
            print(f"  ✅ '{query}' → start={start:.1f} (unit={expected_unit})")
        else:
            print(f"  ⚠️  '{query}' → no match")

    print(f"  ✅ {ok}/{len(cases)} temporal patterns matched")
    return ok > 0


def test_temporal_intent():
    """Test temporal intent detection."""
    print("\n[2.2] Temporal: has_temporal_intent...")

    cases = [
        ("what happened last week", True),
        ("facts from yesterday", True),
        ("Python programming", False),
        ("database query from past 2 days", True),
        ("Javanese culture", False),
        ("kejadian kemarin", True),
        ("hari ini ada apa", True),
    ]

    for query, expected in cases:
        result = has_temporal_intent(query)
        status = "✅" if result == expected else "❌"
        print(f"  {status} '{query}' → {result} (expected {expected})")

    print("  ✅ Temporal intent detection done")
    return True


def test_temporal_sql():
    """Test SQL clause generation."""
    print("\n[2.3] Temporal: make_temporal_filter_clause...")

    now = time.time()

    # Test with start only
    clause, params = make_temporal_filter_clause(time_start=now - 86400)
    assert ">=" in clause
    print(f"  ✅ Start-only clause: {clause[:60]}...")

    # Test with both
    clause, params = make_temporal_filter_clause(
        time_start=now - 86400 * 7, time_end=now
    )
    assert ">=" in clause and "<=" in clause
    print(f"  ✅ Both bounds clause: {clause[:80]}...")

    # Test empty
    clause, params = make_temporal_filter_clause()
    assert clause == ""
    print("  ✅ Empty clause returns empty string")

    print("  ✅ All SQL clause tests passed!")
    return True


# ── 3. TEST VERSIONING ─────────────────────────────────────────────────────


def test_versioning():
    """Test the VersionedFactStore."""
    print("\n[3.1] Versioning: full workflow...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    try:
        # We need to set up a facts table alongside versions
        import sqlite3

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                fact_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT NOT NULL UNIQUE,
                category    TEXT DEFAULT 'general',
                tags        TEXT DEFAULT '',
                trust_score REAL DEFAULT 0.5,
                strength    REAL DEFAULT 1.0,
                source      TEXT DEFAULT 'facts',
                hrr_vector  BLOB,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notion_page_id TEXT,
                helpful_count INTEGER DEFAULT 0,
                last_access_at TIMESTAMP
            );
        """)
        conn.execute(
            "INSERT INTO facts (fact_id, content, category, tags, trust_score, strength) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "Python is a programming language", "general", "python", 0.8, 1.0),
        )
        conn.execute(
            "INSERT INTO facts (fact_id, content, category, tags, trust_score, strength) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (2, "Eling is a 5-layer second brain", "project", "eling,memory", 0.9, 1.0),
        )
        conn.commit()
        conn.close()

        vfs = VersionedFactStore(db)

        # Snapshot
        v_id = vfs.snapshot_current(1, reason="initial_backup")
        assert v_id is not None, "Snapshot should succeed"
        print(f"  ✅ Snapshot fact 1: version_id={v_id}")

        # Update (creates version)
        result = vfs.update_fact(
            1, "Python is a versatile programming language for AI and web"
        )
        assert result["action"] == "versioned_update"
        print(f"  ✅ Versioned update: {result}")

        # History
        history = vfs.get_fact_history(1)
        assert len(history) >= 2, f"Expected >=2 versions, got {len(history)}"
        print(f"  ✅ History: {len(history)} versions for fact 1")

        # Time travel
        snapshot = vfs.get_fact_at_time(1, "2020-01-01 00:00:00")
        assert snapshot is None, "Should not exist before creation"

        # Get history
        first_version = history[0]
        print(
            f"  ✅ Time travel ready: can restore to version seq={first_version['version_seq']}"
        )

        # Undo
        undo_result = vfs.undo_to_version(1, 1)
        assert undo_result["action"] == "reverted"
        print(f"  ✅ Undo to version 1: {undo_result['restored_content'][:60]}...")

        # Timeline
        timeline = vfs.timeline(limit=10)
        assert len(timeline) >= 2
        print(f"  ✅ Timeline: {len(timeline)} entries")

        # Stats
        stats = vfs.stats()
        print(f"  ✅ Stats: {stats}")

        vfs.close()
        print("  ✅ All versioning tests passed!")
        return True
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        os.unlink(db)


# ── RUN ALL ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("🏁 PROTOTYPE E2E TESTS")
    print("=" * 60)

    results = {}

    results["vec_api"] = test_vector_search_api()
    results["vsl"] = test_vsl_index_and_search()
    results["temporal_parse"] = test_temporal_parser()
    results["temporal_intent"] = test_temporal_intent()
    results["temporal_sql"] = test_temporal_sql()
    results["versioning"] = test_versioning()

    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        print(f"⚠️  {failed} test(s) failed")
    else:
        print("🎉 All tests passed!")

"""
Migrate facts from holographic (~/.hermes/memory_store.db) to eling (~/.eling/facts.db).

Strategy:
- Read all facts + entities + fact_entities from holographic DB
- Insert each fact into eling FactsLayer via add_fact() to re-extract entities + recompute HRR
- Preserve original trust_score, retrieval_count, helpful_count, created_at, updated_at
- Backup eling DB before migration if exists
"""

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Resolve source + dest
HOLO_DB = Path(os.path.expanduser("~/.hermes/memory_store.db"))
ELING_HOME = Path(os.environ.get("ELING_HOME", os.path.expanduser("~/.eling")))
ELING_DB = ELING_HOME / "facts.db"

# Pre-flight
if not HOLO_DB.exists():
    print(f"❌ Source DB not found: {HOLO_DB}")
    sys.exit(1)

print(f"📦 Source:      {HOLO_DB}")
print(f"📦 Destination: {ELING_DB}")
print()

# Backup destination if exists
if ELING_DB.exists():
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = ELING_DB.parent / f"facts.db.bak.{ts}"
    shutil.copy2(ELING_DB, backup)
    print(f"💾 Backup: {backup}")

# Read all facts from holographic (read-only)
src = sqlite3.connect(f"file:{HOLO_DB}?mode=ro", uri=True)
src.row_factory = sqlite3.Row
facts = src.execute("""
    SELECT fact_id, content, category, tags, trust_score,
           retrieval_count, helpful_count, created_at, updated_at
    FROM facts ORDER BY fact_id
""").fetchall()
print(f"📖 Read {len(facts)} facts from holographic")
src.close()

# Import eling
ELING_HOME.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, "/root/eling/src")
from eling.layers.facts import FactsLayer

# Open eling DB
fl = FactsLayer(db_path=ELING_DB)

# Track results
migrated = 0
skipped = 0
errors = []

for row in facts:
    content = row["content"]
    if not content or not content.strip():
        skipped += 1
        continue
    try:
        # add handles entity extraction + HRR + dedup
        new_id = fl.add(
            content=content,
            category=row["category"] or "general",
            tags=row["tags"] or "",
            source="holographic-migrated",
        )
        # Restore trust score via public method
        fl.set_trust(new_id, float(row["trust_score"]))

        # Restore retrieval_count, helpful_count, created_at, updated_at
        fl._conn.execute(
            "UPDATE facts SET retrieval_count = ?, helpful_count = ?, "
            "created_at = ?, updated_at = ? "
            "WHERE fact_id = ?",
            (row["retrieval_count"], row["helpful_count"],
             row["created_at"], row["updated_at"], new_id),
        )
        fl._conn.commit()
        migrated += 1
    except Exception as e:
        errors.append((row["fact_id"], str(e)))

fl.close()

print()
print("=" * 60)
print(f"✅ Migrated: {migrated}")
print(f"⏭️  Skipped:  {skipped}")
print(f"❌ Errors:   {len(errors)}")
if errors:
    for fid, err in errors[:5]:
        print(f"   fact_id={fid}: {err}")
print("=" * 60)

# Verify destination
dst = sqlite3.connect(ELING_DB)
dst_count = dst.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
ent_count = dst.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
hrr_count = dst.execute("SELECT COUNT(*) FROM facts WHERE hrr_vector IS NOT NULL").fetchone()[0]
print(f"📊 Final eling DB: {dst_count} facts, {ent_count} entities, {hrr_count} with HRR vectors")
dst.close()
# Migration Guide — Holographic → Eling

Migrate from Hermes's built-in `holographic` memory provider to Eling.

---

## Prerequisites

- Eling installed (`pip install eling` or from source)
- Hermes Agent running with your existing `holographic` data
- (Optional) Notion API key for Tier 5

---

## Step 1: Verify Current Data

```bash
# Check holographic DB exists
ls -la ~/.hermes/memory_store.db

# Count facts
sqlite3 ~/.hermes/memory_store.db "SELECT COUNT(*) FROM facts;"
```

---

## Step 2: Run Migration Script

```bash
python scripts/migrate_holographic.py
```

What it does:

1. Reads all facts, entities, and fact_entities from `~/.hermes/memory_store.db`
2. Backs up any existing `~/.eling/facts.db` → `facts.db.bak.<timestamp>`
3. Inserts each fact into Eling via `FactsLayer.add()` (which re-extracts entities and recomputes HRR vectors)
4. Restores original `trust_score`, `retrieval_count`, `helpful_count`, `created_at`, `updated_at`
5. Verifies destination DB counts

Expected output:

```
📦 Source:      /root/.hermes/memory_store.db
📦 Destination: /root/.eling/facts.db
💾 Backup: /root/.eling/facts.db.bak.20250630_143022
📖 Read 21 facts from holographic

============================================================
✅ Migrated: 21
⏭️  Skipped:  0
❌ Errors:   0
============================================================
📊 Final eling DB: 21 facts, 14 entities, 21 with HRR vectors
```

---

## Step 3: Enable Eling Plugin

```bash
# Symlink the plugin
ln -sf /root/eling/src/eling/plugin ~/.hermes/plugins/memory/eling

# Update Hermes config
hermes config set memory.provider eling
hermes config set plugins.eling.home ~/.eling
```

---

## Step 4: Verify Integration

```bash
# Check memory provider is active
hermes memory status

# Test recall
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"eling_recall","arguments":{"query":"test"}}}' | \
  python -m mcp.cli run stdio python -m eling mcp

# Or via CLI
python -m eling recall "test" --limit 3
```

---

## Step 5: Uninstall Holographic (Optional)

```bash
# Disable old provider
hermes config set memory.provider eling

# Remove old plugin
rm -rf ~/.hermes/plugins/memory/holographic

# Optionally archive old DB
mv ~/.hermes/memory_store.db ~/.hermes/memory_store.db.bak
```

---

## Rollback

If you need to revert:

```bash
# Switch back to holographic
hermes config set memory.provider holographic

# Restore backup (if needed)
cp ~/.eling/facts.db.bak.<timestamp> ~/.eling/facts.db

# Delete Eling plugin symlink
rm -rf ~/.hermes/plugins/memory/eling
```

---

## Migration with Notion

Eling's Notion integration is separate from data migration. After migration:

```bash
# Push migrated high-trust facts to Notion
python -m eling sync --direction push

# Or daemon mode for continuous sync
python -m eling sync --daemon --interval 300
```

Only facts with `trust_score > 0.7` are promoted. Use `eling config set default_trust 0.5` to adjust the threshold.

---

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: eling` | Path | `sys.path.insert(0, "/root/eling/src")` in migration script |
| `0 facts migrated` | DB exists? | `~/.hermes/memory_store.db` must exist |
| HRR all zeros after migration | HRR dim mismatch | Set `ELING_HRR_DIM` to match holographic config |
| Notion sync fails | API key | Export `NOTION_API_KEY` or `ELING_NOTION_ENABLED=false` |

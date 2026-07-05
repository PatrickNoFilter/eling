# Eling API Reference

## MCP Server (JSON-RPC stdio)

Eling runs as a JSON-RPC stdio MCP server, compatible with the [Model Context Protocol](https://modelcontextprotocol.io/).

### Start

```bash
python -m eling mcp
# or
eling mcp
```

### Tools

#### `eling_remember`

Store content in the appropriate memory layer. Auto-routes based on content length.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | string | **required** | Content to store |
| `layer` | string | `"auto"` | `"auto"`, `"facts"`, `"kb"`, or `"notion"` |
| `category` | string | `"general"` | Fact category (facts layer) |
| `tags` | string | `` | Comma-separated tags |
| `source` | string | `mcp` | Agent origin (hermes, opencode, openclaw, etc.) |
| `title` | string | `` | Page title (notion layer) |
| `skip_dedup` | boolean | `false` | Skip SHA-256 dedup check |

**Auto-routing logic:**
- `len > 500` or contains markdown headings (`#` / `##`) → **KB**
- `len ≤ 500` → **Facts**

**Response:**
```json
{
  "layer": "facts",
  "id": 42,
  "content": "...",
  "redacted": {"keys": 2}
}
```

---

#### `eling_recall`

Cross-layer semantic search with RRF fusion.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | **required** | Search query |
| `layers` | string[] | `["builtin","facts","kb","code","notion"]` | Layers to search |
| `limit` | integer | `10` | Max results in merged output |
| `source` | string | `` | Filter by agent origin (empty = all agents) |

**Response:**
```json
{
  "query": "hrr dimension",
  "merged": [
    {
      "_layer": "facts",
      "content": "HRR dim should match numpy float32 stride",
      "_rrf_score": 0.0323
    },
    {
      "_layer": "kb",
      "_rrf_score": 0.0164
    }
  ],
  "per_layer": {
    "builtin": [...],
    "facts": [...],
    "kb": [...],
    "code": [...]
  }
}
```

RRF scoring: `score = Σ 1/(60 + rank)` per layer. Empty/missing layers are skipped.

---

#### `eling_reason`

Find facts connecting multiple entities (compositional HRR query).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `entities` | string[] | **required** | Entities to connect (e.g. `["pytest", "HRR"]`) |
| `limit` | integer | `10` | Max results |

**Response:**
```json
[
  {
    "fact_id": 17,
    "content": "HRR vectors used to encode entity relationships in pytest test suite",
    "trust_score": 0.85,
    "entities": ["pytest", "HRR"]
  }
]
```

---

#### `eling_reflect`

Promote a high-trust fact to a Notion page.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fact_id` | integer | **required** | Fact ID to promote |

**Response:**
```json
{
  "fact_id": 17,
  "page_id": "abc123...",
  "promoted": true
}
```

---

#### `eling_probe`

Get all facts about a single entity (from facts layer).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `entity` | string | **required** | Entity name to probe |
| `limit` | integer | `10` | Max results |

---

#### `eling_verify`

Query verification-on-stop status or record a verification event. Active for
agents without built-in verification (OpenCode, OpenClaw, Cursor, Windsurf);
auto-skipped when the host has its own verify-on-stop (Hermes).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | `""` | `""` = query mode. `"passed"`, `"failed"`, `"skipped"` to record |
| `command` | string | `""` | The command that was run (e.g. `"pytest"`) |
| `output` | string | `""` | Command output (truncated to 500 chars) |
| `spec_check` | boolean | `false` | Also run spec-kit conformance verification |

**Response (query mode):**
```json
{
  "active": true,
  "changed_paths": ["src/main.py"],
  "verified": false,
  "needs_verification": true,
  "nudge": "[System: You edited code...]",
  "spec_kit": {
    "detected": true,
    "coverage": {"covered": 3, "uncovered": 2, "total": 5},
    "requirements": [...]
  }
}
```

---

#### `eling_verify_spec`

Run spec-kit conformance verification. Detects spec-kit artifacts
(`specs/<feature>/spec.md`, `plan.md`, `tasks.md`) and checks whether
code files cover each spec requirement.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `changed_files` | string[] | `[]` | Files changed in this session (for targeted coverage) |

**Response:**
```json
{
  "detected": true,
  "summary": "3/5 requirements covered (2 uncovered)",
  "coverage": {"covered": 3, "uncovered": 2, "total": 5},
  "features": ["auth", "payments"],
  "requirements": [
    {"text": "Login must validate email", "covered": true},
    {"text": "Passwords must be hashed with bcrypt", "covered": false}
  ],
  "nudge": "[System: Spec-kit requirements pending verification..."
}
```

---

#### `eling_sync`

Synchronize data between layers (facts→Notion, flush to disk after writes).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `direction` | string | `"all"` | `"push"` (facts→Notion), `"pull"` (Notion→KB), `"flush"` (disk), or `"all"` |
| `layer` | string | `"auto"` | Layer scope |

---

#### `eling_link_stats`

Zettelkasten fact link graph statistics.

**Parameters:** none

**Response:**
```json
{
  "total_links": 42,
  "linked_facts": 18,
  "avg_links_per_fact": 2.33
}
```

---

#### `eling_linked_facts`

Get facts linked to a given fact_id (Zettelkasten-style), ordered by link weight.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fact_id` | integer | **required** | Fact ID to get linked facts for |
| `limit` | integer | `10` | Max results |

**Response:**
```json
[
  {
    "fact_id": 5,
    "content": "HRR dim should match numpy float32 stride",
    "category": "general",
    "trust_score": 0.85,
    "weight": 0.42
  }
]
```

---

#### `eling_evolve`

Trigger a memory evolution pass: scan all facts for near-duplicate pairs and merge them.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `threshold` | float | `0.65` | Jaccard similarity threshold for merge |

**Response:**
```json
{
  "merged": 3,
  "candidates_checked": 120
}
```

---

#### `eling_snapshot`

Create a named snapshot of the facts database. Use before destructive operations (evolution, decay, bulk edits) for safe rollback.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `reason` | string | `""` | Why the snapshot is taken (e.g. `"pre_evolution"`) |

**Response:**
```json
{
  "snapshot_id": "20260704-013052-853",
  "path": "/root/.eling/snapshots/20260704-013052-853.db",
  "created_at": "2026-07-04T01:30:52",
  "reason": "pre_evolution",
  "size_bytes": 4096,
  "fact_count": 42
}
```

---

#### `eling_list_snapshots`

List all available snapshots, newest first.

**Parameters:** none

**Response:**
```json
{
  "snapshots": [
    {
      "snapshot_id": "20260704-013052-853",
      "fact_count": 42,
      "reason": "pre_evolution"
    }
  ]
}
```

---

#### `eling_rollback`

Rollback the facts database to a named snapshot. The current database is auto-snapshotted before the rollback so it can be undone.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `snapshot_id` | string | **required** | Snapshot ID to restore (from `eling_list_snapshots`) |

**Response:**
```json
{
  "snapshot_id": "20260704-013052-853",
  "restored_from": "/root/.eling/snapshots/20260704-013052-853.db",
  "restored_to": "/root/.eling/facts.db",
  "fact_count": 42,
  "current_backup": "20260704-013215-456"
}
```

---

#### `eling_stats`

Get statistics about all memory layers.

**Parameters:** none

**Response:**
```json
{
  "home": "/root/.hermes/eling",
  "facts": {
    "total_facts": 42,
    "total_entities": 28,
    "with_hrr": 42,
    "avg_trust": 0.72
  },
  "kb": {
    "total_chunks": 156,
    "total_sources": 12,
    "total_errors": 0
  },
  "code_available": true,
  "notion_available": true,
  "privacy": {
    "total_redacted": 3,
    "total_dedup_hits": 7
  },
  "hooks": {
    "total_handlers": 15,
    "hooks_with_handlers": 15
  }
}
```

---

#### `eling_search_temporal`

Search facts by time range using natural language (English or Indonesian).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | `""` | Time range expression like "last 3 days", "this week", "kemarin", "hari ini" |
| `category` | string | `""` | Optional category filter |
| `since_days` | integer | `0` | Override: search back N days (e.g., 7 = last 7 days) |
| `source` | string | `""` | Filter by agent source |
| `limit` | integer | `20` | Max results |

**Supported patterns:**

| Language | Examples |
|----------|----------|
| English | `today`, `yesterday`, `this week`, `last month`, `last 3 days`, `last 7 days`, `last 30 days` |
| Indonesian | `hari ini`, `kemarin`, `minggu ini`, `bulan lalu`, `3 hari terakhir`, `7 hari terakhir`, `30 hari terakhir` |

**Response:**
```json
[
  {
    "fact_id": 42,
    "content": "Eling v0.6.0 released",
    "category": "release",
    "trust_score": 0.85,
    "created_at": "2026-07-04T10:30:00"
  }
]
```

---

#### `eling_versioned_update`

Update a fact's content while preserving the previous version.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fact_id` | integer | **required** | Fact ID to update |
| `content` | string | **required** | New content |
| `reason` | string | `""` | Why the update was made (e.g., "corrected typo") |

**Response:**
```json
{
  "fact_id": 42,
  "version_id": 3,
  "previous": "Original content...",
  "new": "Updated content..."
}
```

---

#### `eling_get_version_history`

Get all versions of a fact, from newest to oldest.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fact_id` | integer | **required** | Fact ID to get history for |

**Response:**
```json
[
  {
    "version_id": 2,
    "content": "Latest content...",
    "changed_at": "2026-07-04T12:00:00",
    "reason": "corrected typo",
    "embedding_dim": 1024
  },
  {
    "version_id": 1,
    "content": "Original content...",
    "changed_at": "2026-07-04T10:00:00",
    "reason": "initial"
  }
]
```

---

#### `eling_undo_to_version`

Rollback a fact to a previous version (also creates a new version entry).

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fact_id` | integer | **required** | Fact ID to rollback |
| `version_id` | integer | **required** | Version ID to restore |

**Response:**
```json
{
  "fact_id": 42,
  "version_id": 5,
  "restored_from": 2
}
```

---

#### `eling_versioning_stats`

Get statistics about the versioning system across the fact store.

**Parameters:** none

**Response:**
```json
{
  "versioned_facts": 42,
  "total_versions": 156,
  "version_operations": 114
}
```

---

## CLI Reference

### Commands

```
eling <command> [options]
```

| Command | Description | Example |
|---------|-------------|---------|
| `remember` | Store content | `eling remember "Use pytest -x to stop on first failure" --tags testing --source opencode` |
| `recall` | Search layers | `eling recall "testing patterns" --limit 5 --source hermes` |
| `reason` | Connect entities | `eling reason pytest HRR facts` |
| `probe` | Facts about entity | `eling probe pytest` |
| `reflect` | Promote to Notion | `eling reflect 17` |
| `verify` | Query verification status | `eling verify` |
| `verify-spec` | Run spec-kit conformance check | `eling verify-spec --changed-files src/main.py` |
| `snapshot` | Snapshot facts DB | `eling snapshot --reason "pre_evolution"` |
| `list-snapshots` | List all snapshots | `eling list-snapshots` |
| `rollback` | Restore facts DB from snapshot | `eling rollback <snapshot_id>` |
| `link-stats` | Zettelkasten link graph stats | `eling link-stats` |
| `linked-facts` | Get linked facts for fact_id | `eling linked-facts 1` |
| `evolve` | Merge near-duplicate facts | `eling evolve --threshold 0.7` |
| `sync` | Sync layers | `eling sync --direction push` |
| `stats` | Show brain stats | `eling stats` |
| `mcp` | Run MCP server | `eling mcp` |
| `config` | Manage config | `eling config get hrr_dim` |
| `install-opencode` | Install OpenCode lifecycle plugin | `eling install-opencode` |
| `install-zero` | Install Zero hooks + skill + MCP | `eling install-zero` |
| `init-rules` | Write steering rules for AI agents | `eling init-rules` |
| `search-temporal` | Search facts by time range | `eling search-temporal "last 3 days" --category testing` |
| `versioned-update` | Update fact with versioning | `eling versioned-update 1 "new text" --reason "correction"` |
| `version-history` | Show version history of a fact | `eling version-history 1` |
| `undo-to-version` | Rollback fact to a version | `eling undo-to-version 1 --version-id 0` |
| `versioning-stats` | Versioning system statistics | `eling versioning-stats` |

### `eling remember`

```
usage: eling remember [-h] [--layer {auto,facts,kb,notion}]
                      [--category CATEGORY] [--tags TAGS]
                      [--source SOURCE] [--title TITLE]
                      content
```

### `eling recall`

```
usage: eling recall [-h] [--limit LIMIT] [--layers LAYERS] [--source SOURCE] query

optional arguments:
  --layers LAYERS      comma-separated: "facts,kb,code"
  --source SOURCE      filter by agent origin: "hermes", "opencode", etc.
```

### `eling reason`

```
usage: eling reason [-h] [--limit LIMIT] entities [entities ...]
```

### `eling sync`

```
usage: eling sync [-h] [--direction {push,pull,flush,all}]
                  [--layer {auto,facts,notion,kb}]
                  [--daemon] [--interval INTERVAL]
                  [--once] [--state-file STATE_FILE]
```

### `eling snapshot`

```
usage: eling snapshot [-h] [--reason REASON]
```

### `eling rollback`

```
usage: eling rollback [-h] snapshot_id
```

### `eling install-opencode`

```
usage: eling install-opencode [-h] [--dry-run]
```

### `eling install-zero`

```
usage: eling install-zero [-h] [--dry-run] [--zero-config-dir ZERO_CONFIG_DIR]
```

### `eling init-rules`

```
usage: eling init-rules [-h] [--project-dir PROJECT_DIR]
                        [--agent {cursor,claude_code,opencode,generic}]
                        [--dry-run]
```

### `eling config`

```
usage: eling config <subcommand> [options]

Subcommands:
  get [key]            Get config value(s)
  set <key> <value>    Set a config value
  unset <key>          Remove a config value
  ls                   List all config keys with sources
  init                 Write default config.json
  schema               Show full config schema
```

---

## Config Reference

### Config Files

Eling resolves configuration in this priority order:

1. **Hermes plugin config** → `~/.hermes/config.yaml` → `plugins.eling.*`
2. **Environment** → `ELING_*` prefixed vars
3. **JSON config** → `~/.eling/config.json`
4. **Defaults** → hardcoded in `config.py`

### All Keys

| Key | Env | Default | Type | Description |
|-----|-----|---------|------|-------------|
| `home` | `ELING_HOME` | `$HERMES_HOME/eling` | path | Eling data directory |
| `hrr_dim` | `ELING_HRR_DIM` | `512` | int | HRR vector dimension (must match numpy float32) |
| `default_trust` | `ELING_DEFAULT_TRUST` | `0.5` | float | Initial trust score for new facts |
| `min_trust` | `ELING_MIN_TRUST` | `0.0` | float | Minimum trust for recall results |
| `notion_enabled` | `ELING_NOTION_ENABLED` | `true` | bool | Enable Notion layer |
| `codegraph_enabled` | `ELING_CODEGRAPH_ENABLED` | `true` | bool | Enable Code layer |
| `dedup_cache_size` | `ELING_DEDUP_CACHE_SIZE` | `1000` | int | SHA-256 dedup LRU cache size |
| `auto_sync_turns` | `ELING_AUTO_SYNC_TURNS` | `true` | bool | Auto-sync every N turns |
| `embedding_model` | `ELING_EMBEDDING_MODEL` | `""` | string | Model name for embeddings. For sentence-transformers: `all-MiniLM-L6-v2`. For Mistral API: uses `mistral-embed` (1024 dims) when provider is `mistral` |
| `embedding_provider` | `ELING_EMBEDDING_PROVIDER` | `""` | string | Embedding provider: `""` (auto-detect), `sentence-transformers`, or `mistral` |

---

## Python API

### Brain

```python
from eling.brain import Brain

brain = Brain(
    home="/path/to/data",        # default: ~/.eling
    notion_api_key="ntkn_...",   # optional
    notion_parent_id="page_...", # optional
    project_path="/path/to/code",# optional, for code layer
    hrr_dim=1024,                # default: 512
    embedding_model="all-MiniLM-L6-v2",  # optional, requires sentence-transformers
)

# Store (with agent source for multi-agent setups)
brain.remember("Content", layer="auto", source="hermes")

# Search (filter by agent source or leave empty for cross-agent)
results = brain.recall("query", limit=10, source="opencode")

# Connect
facts = brain.reason(["pytest", "HRR"])

# Probe a single entity
facts = brain.probe("pytest")

# Sync layers (push facts→Notion + flush to disk)
result = brain.sync(direction="all")

# Snapshot & rollback (v0.5.1)
snap = brain.snapshot(reason="pre_evolution")
brain.rollback(snap["snapshot_id"])
snapshots = brain.list_snapshots()

# Temporal search (v0.6.0) - natural language time range
results = brain.search_temporal("last 3 days", category="testing")
results = brain.search_temporal("kemarin")  # Indonesian support

# Per-fact versioning (v0.6.0) - append-only updates
versioned = brain.versioned_update(1, "New content", reason="corrected typo")
history = brain.get_version_history(1)
undo = brain.undo_to_version(1, version_id=0)
vstats = brain.versioning_stats()

# Stats
stats = brain.stats()

# Promote
brain.reflect(fact_id=17)

# Stats
brain.stats()

# Sync
brain.sync(direction="all")

# Cleanup
brain.close()
```

### Config

```python
from eling.config import (
    DEFAULTS,
    describe_config,
    get_config,
    resolve_config,
    set_config_key,
    remove_config_key,
)

# Resolve with full fallback chain
cfg = resolve_config()
print(cfg["hrr_dim"])  # 512 (or from env/config)

# Read/write config file
set_config_key("hrr_dim", 1024, home="/home/user/.eling")

# Get schema
schema = describe_config()
```

### Hooks

```python
from eling.hooks import HookRegistry, HOOK_DECISION_MADE

registry = HookRegistry()

# Register custom handler
def my_handler(name: str, ctx: dict) -> dict:
    print(f"Hook {name} fired with: {ctx}")
    return {"ok": True}

registry.register(HOOK_DECISION_MADE, my_handler)
results = registry.fire(HOOK_DECISION_MADE, {"content": "test"})
```

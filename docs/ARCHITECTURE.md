# Eling Architecture

> **Eling** — Unified second brain for AI agents. Five memory layers, dual MCP servers (notion-only `eling` + local-layers `as_brain`), zero mandatory external dependencies. v0.7.3 splits the MCP server into two focused servers (notion-only `eling` + local `as_brain`); v0.7.2 adds FactMemoryProvider, lazy numpy, Hermes session-end flush; v0.6.0 adds temporal queries, per-fact versioning, Mistral vector embeddings; v0.5.1 adds crash resilience fixes; v0.5.0 added snapshot/rollback, vector embeddings, steering rules.

```
eling/
├── mcp_server.py         — JSON-RPC stdio server (notion-only, 5 tools)
├── as_brain/
│   └── mcp_server.py     — JSON-RPC stdio server (local layers, 15+ tools)
├── brain.py          — Orchestrator: routing + RRF fusion + sync + snapshot
├── config.py         — Layered config: env → json → defaults
├── hooks.py          — 15 lifecycle hooks + HookRegistry
├── verify_on_stop.py — Verification ledger + nudge builder + spec-kit wiring
├── spec_kit.py       — Spec-kit artifact parser + coverage analyzer
├── snapshot.py       — Git-like snapshot & rollback for facts DB
├── rules.py          — Steering rules generator (Cursor, Claude Code, OpenCode)
├── privacy.py        — PII/secret stripping (19 patterns)
├── compress.py       — SHA-256 dedup + length compression
├── cli.py            — `eling` CLI (18 subcommands)
├── fact_memory_provider.py — Standalone facts layer provider (no Brain)
├── opencode_plugin/  — Bundled OpenCode lifecycle plugin
│   └── eling-memory.js
└── layers/
    ├── builtin.py    — Tier 1: MEMORY.md / USER.md loader
    ├── facts.py      — Tier 2: SQLite + HRR + BM25 + Embeddings + Trust + Zettelkasten + Temporal + Versioning
    ├── embeddings.py — Optional vector embeddings (Mistral API + sentence-transformers)
    ├── hrr.py        — Holographic Reduced Representations (numpy)
    ├── code.py       — Tier 3: CodeLayer wrapper
    ├── code_index.py — Pure-Python AST+regex code indexer
    ├── kb.py         — Tier 4: FTS5 + porter + trigram + RRF
    └── notion.py     — Tier 5: httpx Notion API client
```

---

## 🧠 Five Cognitive Tiers

```
┌──────────────────────────────────────────────────────────────┐
│ Tier 1: BUILTIN     MEMORY.md + USER.md             ~0ms    │
│          Always-on, ~2.2 KB / 1.4 KB                        │
├──────────────────────────────────────────────────────────────┤
│ Tier 2: FACTS       SQLite + HRR + BM25 + Trust    ~14ms    │
│          Compositional reasoning, trust decay                │
├──────────────────────────────────────────────────────────────┤
│ Tier 3: CODE        Pure Python CodeIndex          ~10-50ms │
│          AST (Python) + regex (15+ langs)                   │
├──────────────────────────────────────────────────────────────┤
│ Tier 4: KB          FTS5 + porter + trigram + RRF  ~50ms    │
│          Knowledge corpus with typo correction               │
├──────────────────────────────────────────────────────────────┤
│ Tier 5: NOTION      httpx direct API               ~200-500ms│
│          Token bucket, exponential backoff, 5-min cache     │
└──────────────────────────────────────────────────────────────┘
```

### Tier 1 — Builtin
Reads Hermes `MEMORY.md` and `USER.md` files directly. Always available, no setup. Provides raw text blocks ranked by BM25 similarity.

### Tier 2 — Facts
SQLite-backed fact store with:
- **HRR** (Holographic Reduced Representations, numpy) — compositional vector binding for multi-entity reasoning
- **BM25** — FTS5 porter stemming full-text search
- **Jaccard** — entity co-occurrence scoring
- **Vector embeddings** (optional, v0.5.1) — sentence-transformers or **Mistral API** integration via EmbeddingIndex in layers/embeddings.py. Cosine similarity scores blended into hybrid ranking. Enable with Brain(embedding_model="all-MiniLM-L6-v2") or set MISTRAL_API_KEY for the API-based provider.
- **Trust scoring** — facts have trust_score (0.0-1.0), decays over time, boosted by user corrections
- **Temporal search** (v0.6.0) — natural language time-range queries in English and Indonesian (search_temporal())
- **Per-fact versioning** (v0.6.0) — append-only fact_versions table preserves history; versioned_update(), get_version_history(), undo_to_version()

Hybrid search formula: `score = BM25 * 0.4 + Jaccard * 0.3 + HRR * 0.3 + Embedding * 0.1`

Use `brain.reason(entities)` to find facts connecting multiple entities via HRR unbinding.

#### Zettelkasten Memory Linking (v0.3.0)

Every new fact is automatically linked to semantically similar existing facts:

1. BM25 retrieves candidate facts (up to 20)
2. Jaccard similarity reranks candidates on token overlap
3. Bidirectional `fact_links` table edges created when Jaccard ≥ `LINK_THRESHOLD` (0.25)
4. Links are weighted by similarity score and updated (take max weight)

Inspired by A-MEM (Xu et al., 2025): *"Agentic Memory for LLM Agents"*.

#### Memory Evolution (v0.3.0)

A periodic maintenance pass that merges near-duplicate facts:

1. Scans all fact pairs for Jaccard similarity ≥ `EVOLVE_MERGE_THRESHOLD` (0.65)
2. Merges: content combined (longer wins or concatenated), trust averaged, entities unioned
3. Transfers `fact_links` from the deleted fact to the survivor
4. Recomputes HRR vector for the merged fact
5. Runs automatically on `idle_30min` hook; also available via `eling_evolve` MCP tool

### Tier 3 — Code
Pure-Python code intelligence with **zero external dependencies**:
- Python files → `ast.walk()` extracts `ClassDef`, `FunctionDef`, `AsyncFunctionDef`
- Non-Python files → regex patterns for JS, TS, Rust, Go, Java, C++, Ruby, PHP, Swift, Kotlin, C#, Scala, Shell, Lua, Elixir
- Cache to `.eling/code_index.json`
- Skips `__pycache__`, `node_modules`, `.venv`, `target`, files >512 KB
- Search: case-insensitive substring, ranked exact→prefix→substring

Replaces the previous `codegraph` (Node.js) external dependency.

### Tier 4 — KB
FTS5 knowledge base ported from [context-mode](https://github.com/nousresearch/hermes-agent) (MIT):
- **Porter stemming** — language-aware term normalization
- **Trigram index** — substring matching for partial/typo-tolerant queries
- **RRF fusion** — reciprocal rank fusion between porter and trigram results
- **Levenshtein** — single-character typo correction
- **Window extraction** — snippet highlighting around matched terms

### Tier 5 — Notion (Online Memory)

Notion is what makes eling **human-readable and recoverable**. High-trust facts are synced to your Notion vault as permanent, well-formatted pages — so a human can browse, edit, and share what the agent learned.

**Technical stack:** Direct httpx API client (replaces `awkoy/notion-mcp-server` subprocess):
- Token bucket rate limiter (3 req/s Notion limit)
- Exponential backoff on 429/5xx
- 5-minute idempotency cache for read ops
- Response slimming (strips raw API fluff)

**Vault structure:** The opinionated layout keeps the main page as a clean index of credential children, with all log entries auto-routed to a dedicated child page (never write to the main vault page directly).

**Why Notion?** It's the only tier that survives local data loss. Even if your SQLite databases are wiped, the Notion vault retains curated facts and permanent project knowledge — a recoverable brain.

---

## 🔄 Data Flow

### remember(content) — Store
```
User input → PrivacyPipeline (SHA-256 dedup → 19 secret patterns)
           → LLM compressor (optional)
           → Auto-routing: >500 chars or markdown headings → KB
                           ≤500 chars → Facts
                           layer="notion" → Notion API
           → Post-tool-use hook fires
```

### recall(query) — Retrieve
```
Query → Parallel layer search:
         ├─ builtin.search(query)
         ├─ facts.search(query)    → BM25 + HRR
         ├─ kb.search(query)       → FTS5 porter + trigram + RRF
         ├─ code.search(query)     → CodeIndex substring
         └─ notion.search(query)   → Notion API (if configured)
       → RRF fusion (k=60):
         score = Σ 1/(k + rank)
       → Ranked merged results + per-layer raw results
```

### reason(entities) — Connect
```
[entity_A, entity_B] → facts.reason(entities)
                     → HRR unbinding per entity
                     → Jaccard intersection scoring
                     → Ranked facts connecting all entities
```

### reflect(fact_id) — Promote
```
fact_id → facts.get(fact_id)
        → Notion.create_page(title="💡 ...", content=body)
        → Returns page_id
```

---

## 🔧 Config System

Layered fallback chain (high → low priority):

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Hermes plugin config | `hermes config set plugins.eling.hrr_dim 1024` |
| 2 | Environment variable | `ELING_HRR_DIM=1024` |
| 3 | JSON config file | `~/.eling/config.json` |
| 4 | Hardcoded default | `hrr_dim: 512` |

CLI: `eling config get`, `set`, `unset`, `ls`, `init`, `schema`.

Keys:

| Key | Env | Default | Type |
|-----|-----|---------|------|
| `home` | `ELING_HOME` | `$HERMES_HOME/eling` | path |
| `hrr_dim` | `ELING_HRR_DIM` | `512` | int |
| `default_trust` | `ELING_DEFAULT_TRUST` | `0.5` | float |
| `min_trust` | `ELING_MIN_TRUST` | `0.0` | float |
| `notion_enabled` | `ELING_NOTION_ENABLED` | `true` | bool |
| `codegraph_enabled` | `ELING_CODEGRAPH_ENABLED` | `true` | bool |
| `dedup_cache_size` | `ELING_DEDUP_CACHE_SIZE` | `1000` | int |
| `auto_sync_turns` | `ELING_AUTO_SYNC_TURNS` | `true` | bool |

---

## 🔌 Hermes Plugin

Eling integrates as a Hermes memory provider:

```
~/.hermes/plugins/memory/eling → symlink → /root/eling/src/eling/plugin/
```

Activates 15 lifecycle hooks via `ElingMemoryProvider`:

- Session: `session_start`, `session_end`
- Message: `pre_user_message`, `post_user_message`, `post_assistant_message`
- Tool: `pre_tool_use`, `post_tool_use`
- Memory: `decision_made`, `file_edit`, `error_occurred`, `compaction`
- Sync: `sync_start`, `sync_complete`, `sync_error`
- Idle: `idle_30min`

---

## 🔁 Sync System

Bidirectional synchronization between layers:

| Direction | Source → Target | Description |
|-----------|----------------|-------------|
| `push` | Facts → Notion | High-trust (>0.7) facts promoted as Notion pages |
| `pull` | Notion → KB | Recent Notion pages pulled into local KB |
| `flush` | Memory → Disk | Pending writes persisted |
| `all` | Full cycle | push + pull + flush |

Daemon mode: `eling sync --daemon --interval 300` (5-minute auto-sync).

Sync state persisted to `~/.eling/sync_state.json` (tracks last sync time, counters, last 5 errors).

---

## 🛡️ Privacy Pipeline

19 secret patterns detected and redacted before any storage:

| Type | Pattern |
|------|---------|
| GitHub PAT | `ghp_[A-Za-z0-9]{36}` |
| OpenAI API | `sk-[A-Za-z0-9]{40,}` |
| Anthropic API | `sk-ant-[A-Za-z0-9]{40,}` |
| Slack Token | `xox[baprs]-` + alphanumeric |
| Discord Token | `[MN][A-Za-z\\d]{23,25}\\.\\w{6}\\.\\w{27,38}` |
| AWS Keys | `AKIA` + 16 alphanumeric |
| JWT | `eyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}` |
| SSH Keys | `-----BEGIN (RSA|OPENSSH|EC) PRIVATE KEY-----` |
| +11 more | API keys, tokens, bearer auth, connection strings |

---

## 📊 MCP Tools

| Tool | Arguments | Returns |
|------|-----------|---------|
| `eling_remember` | content, layer, category, tags, **source**, title, skip_dedup | `{layer, id, redacted}` |
| `eling_recall` | query, layers[], limit, **source** | `{merged[], per_layer{}}` |
| `eling_reason` | entities[], limit | `facts[]` connecting all entities |
| `eling_probe` | entity, limit | `facts[]` about entity |
| `eling_reflect` | fact_id | `{page_id}` in Notion |
| `eling_sync` | direction, layer | `{pushed, pulled, errors}` |
| `eling_stats` | — | `{facts, kb, code, notion, privacy, hooks, embeddings}` |
| `eling_think` | query, entities[], limit | `{synthesis, results, gap_analysis}` |
| `eling_export` | format, path | `{format, bytes, preview}` |
| `eling_verify` | status, command, output, **spec_check** | `{active, changed_paths, nudge, spec_kit}` |
| `eling_verify_spec` | changed_files[] | `{detected, coverage, requirements, nudge}` |
| `eling_link_stats` | — | `{total_links, linked_facts, avg_links_per_fact}` |
| `eling_linked_facts` | fact_id, limit | `[{fact_id, content, weight}]` |
| `eling_evolve` | threshold | `{merged}` |
| `eling_snapshot` | reason | `{snapshot_id, path, fact_count}` |
| `eling_list_snapshots` | — | `{snapshots[]}` |
|| `eling_rollback` | snapshot_id | `{snapshot_id, fact_count, current_backup}` |
|| `eling_search_temporal` | query, category, since_days, source, limit | `[{fact_id, content, created_at, category, trust_score}]` |
|| `eling_versioned_update` | fact_id, content, reason | `{fact_id, version_id, previous, new}` |
|| `eling_get_version_history` | fact_id | `[{version_id, content, changed_at, reason}]` |
|| `eling_undo_to_version` | fact_id, version_id | `{fact_id, version_id, restored_from}` |
|| `eling_versioning_stats` | — | `{versioned_facts, total_versions, version_operations}` |

---

## 📐 Spec-kit Verification

Eling integrates [spec-kit](https://github.com/github/spec-kit) (Spec-Driven
Development) as a **code verification layer**. When a project has spec-kit
artifacts under `specs/`, eling automatically:

1. **Discovers** feature directories (`specs/*/`)
2. **Parses** `spec.md` → extracts requirements, user stories, acceptance criteria
3. **Parses** `plan.md` → implementation architecture sections
4. **Parses** `tasks.md` → task breakdown with file references
5. **Matches** requirements against code files via term overlap in file paths
6. **Reports** coverage stats + uncovered requirements via MCP tool or verify nudge

### Flow

```
spec-kit artifacts              eling
┌─────────────────┐     ┌──────────────────┐
│ specs/auth/     │     │ SpecKitVerifier  │
│   spec.md       │────→│ .detect()        │
│   plan.md       │────→│ .load()          │
│   tasks.md      │────→│ .verify(files)   │
└─────────────────┘     │                  │
                        │ Coverage report  │──→ eling_verify_spec MCP tool
code changes            │ + nudge message  │──→ eling_verify(spec_check=true)
┌─────────────────┐     └──────────────────┘
│ src/auth/       │
│   login.py      │────→ term matching
│   jwt.py        │
└─────────────────┘
```

The `SpecKitVerifier` class in `spec_kit.py` is the core engine, used by both
`verify_on_stop.py` (for automatic nudge) and `eling_verify_spec` (for explicit
MCP tool queries).

---

## 📸 Snapshot & Rollback (v0.5.1)

Git-like version control for the facts database, inspired by Memoria's Git-for-Data:

### Mechanism

Snapshots are **file-level copies** of `facts.db` with automatic WAL checkpointing for consistency. The current database is auto-snapshotted before each rollback (undo for the undo).

```
create_snapshot(db_path):
  1. PRAGMA wal_checkpoint(TRUNCATE)
  2. shutil.copy2(db_path, snapshots/<id>.db)
  3. Count facts, write metadata JSON
  4. Prune oldest beyond SNAPSHOT_KEEP (default 5)

rollback(snapshot_id, db_path):
  1. Auto-snapshot current DB (pre_rollback_<id>)
  2. shutil.copy2(snapshot.db, db_path)
  3. PRAGMA wal_checkpoint(TRUNCATE)
  4. Brain re-opens FactsLayer from restored DB
```

Available via:
- **Python**: `brain.snapshot(reason)`, `brain.rollback(id)`, `brain.list_snapshots()`
- **CLI**: `eling snapshot --reason`, `eling list-snapshots`, `eling rollback <id>`
- **MCP**: `eling_snapshot`, `eling_list_snapshots`, `eling_rollback`

---

## 🎯 Steering Rules (v0.5.1)

`rules.py` generates agent-specific steering files that teach AI agents **when** to use eling's MCP tools. Three rule sets:

| Rule Set | Content |
|----------|---------|
| `memory` | When to store, retrieve, probe, snapshot |
| `session-lifecycle` | Bootstrap at conversation start, persist at end |
| `memory-hygiene` | Evolution, contradiction resolution, health checks |

Auto-detects Cursor (`.cursor/rules/`), Claude Code (`.claude/rules/`), OpenCode (`AGENTS.md`), Kiro (`.kiro/`), Gemini (`.gemini/`). Fallback writes `ELING_MEMORY.md`.

```
eling init-rules --project-dir /path/to/project
```

---

Each tool accepts an optional `source` parameter for multi-agent identity. When
`source` is set during remember, the fact is tagged with that agent name. When
set during recall, results are filtered to that agent's memories only.
Empty `source` = all agents.

### Multi-agent setup

Configure each agent to connect to the same Eling MCP server:

```yaml
# Hermes (~/.hermes/config.yaml)
mcp_servers:
  eling:
    command: python -m eling.mcp_server

# OpenCode (~/.opencode/config.yaml)
mcpServers:
  eling:
    command: python -m eling.mcp_server

# OpenCLAW (~/.openclaw/config.yaml)
mcpServers:
  eling:
    command: python -m eling.mcp_server
```

Each agent sets `source` when storing or querying. Facts are shared across
agents (cross-layer RRF finds relevant results from any origin) but scoped
search is available by passing `source="agent_name"`.

---

## 🧪 Test Architecture

```
tests/
├── test_facts.py      — Fact CRUD, HRR, search, trust
├── test_kb.py         — KB index, search, dedup, web fetch
├── test_hrr.py        — HRR binding/unbinding math
├── test_brain.py      — remember, recall (RRF), reason, reflect
├── test_privacy.py    — 48 test cases, 19 pattern types
├── test_config.py     — Config load, env, merge, errors
├── test_hooks.py      — HookRegistry + handlers
├── test_sync.py       — Sync push/pull/flush
├── test_builtin.py    — BuiltinLayer search
└── test_cli.py        — CLI subcommands (20 tests)
```

Total: **408 tests**. Coverage target: >80%.

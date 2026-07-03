# Eling Architecture

> **Eling** — Unified second brain for Hermes Agent. Five memory layers, one MCP server, zero external dependencies.

```
eling/
├── mcp_server.py     — JSON-RPC stdio server (11 tools)
├── brain.py          — Orchestrator: routing + RRF fusion + sync
├── config.py         — Layered config: env → json → defaults
├── hooks.py          — 15 lifecycle hooks + HookRegistry
├── verify_on_stop.py — Verification ledger + nudge builder + spec-kit wiring
├── spec_kit.py       — Spec-kit artifact parser + coverage analyzer
├── privacy.py        — PII/secret stripping (19 patterns)
├── compress.py       — SHA-256 dedup + length compression
├── cli.py            — `eling` CLI (remember/recall/reason/verify/verify-spec/stats/mcp/config/sync)
└── layers/
    ├── builtin.py    — Tier 1: Hermes MEMORY.md / USER.md loader
    ├── facts.py      — Tier 2: SQLite + HRR + BM25 + Trust scoring
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
- **HRR** (Holographic Reduced Representations, `numpy`) — compositional vector binding for multi-entity reasoning
- **BM25** — FTS5 porter stemming full-text search
- **Jaccard** — entity co-occurrence scoring
- **Trust scoring** — facts have `trust_score` (0.0–1.0), decays over time, boosted by user corrections

Use `brain.reason(entities)` to find facts connecting multiple entities via HRR unbinding.

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

### Tier 5 — Notion
Direct httpx API client (replaces `awkoy/notion-mcp-server` subprocess):
- Token bucket rate limiter (3 req/s Notion limit)
- Exponential backoff on 429/5xx
- 5-minute idempotency cache for read ops
- Response slimming (strips raw API fluff)

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
| `eling_stats` | — | `{facts, kb, code, notion, privacy, hooks}` |
| `eling_think` | query, entities[], limit | `{synthesis, results, gap_analysis}` |
| `eling_export` | format, path | `{format, bytes, preview}` |
| `eling_verify` | status, command, output, **spec_check** | `{active, changed_paths, nudge, spec_kit}` |
| `eling_verify_spec` | changed_files[] | `{detected, coverage, requirements, nudge}` |

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

Total: **240 tests** (220 non-CLI + 20 CLI). Coverage target: >80%.

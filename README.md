<div align="center">

# 🧠 Eling

**Lightweight memory, powerful retrieval — 5-tier second brain for AI agents**

HRR reasoning · MCP tools · temporal queries · per-fact versioning · vector search · Zettelkasten linking · memory evolution · spec-kit verification · conditional + universal verify-on-stop · ELING_HOME override · handshake agent auto-attribution · FactMemoryProvider

*"Eling" (Javanese): to remember, to be conscious, to be aware*

[![PyPI](https://img.shields.io/pypi/v/eling)](https://pypi.org/project/eling/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)

</div>

---

## ✨ What is Eling?

Eling is a **lightweight, unified second brain** for AI agents. It merges 5 memory tiers via two MCP servers — no external databases, no cloud services needed for local operation (though it optionally syncs to Notion for human readability).

Think of it as one memory stack that serves **both the agent and the human**:

```
🧠 Tier 5: NOTION   — online brain, persistent, human-readable (optional)
📚 Tier 4: KB       — FTS5 knowledge corpus for long-form knowledge
🕸️ Tier 3: CODE     — codegraph symbol intelligence
💎 Tier 2: FACTS    — SQLite + HRR + BM25 hybrid with trust scoring
📌 Tier 1: BUILTIN   — Hermes MEMORY.md / USER.md (always-on prompt context)
```

### How the tiers work together

| Tier | What it stores | How it's queried | Persistence |
|------|---------------|-------------------|-------------|
| **🧠 Notion** | Permanent pages, project plans, vault entries | `eling_reflect` promotes facts; `eling_sync push` syncs | Cloud — human-viewable, survives everything |
| **📚 KB** | Articles, docs, long-form knowledge chunks | FTS5 full-text search | Local SQLite — persistent |
| **🕸️ Code** | Function symbols, imports, class hierarchies | Codegraph traversal | Local SQLite — auto-indexed |
| **💎 Facts** | Short facts, preferences, observations | HRR + BM25 + trigram hybrid with trust scores | Local SQLite — append-only, versioned |
| **📌 Builtin** | Agent identity, user profile, conventions | Always in prompt context (MEMORY.md / USER.md) | Hermes config files |

### 🧠 Notion as Online Memory

Tier 5 (Notion) is what makes eling **human-readable**. While tiers 1–4 live locally as SQLite databases, Tier 5 optionally syncs high-trust facts to your Notion vault as permanent, well-formatted pages:

- **`eling_reflect <fact_id>`** — promote a high-trust fact to a Notion page
- **`eling_sync --direction push`** — batch-sync all high-trust facts to Notion
- **`eling_sync --direction pull`** — pull Notion pages back into the knowledge base
- **`eling_sync --direction all`** — bidirectional sync

This gives you a **recoverable brain**: even if your local SQLite databases are lost, your Notion vault retains the curated facts and permanent knowledge. The opinionated vault structure keeps the main page as a clean index of credential children, with all log entries auto-routed to a dedicated child page.

## 🚀 Quick Start

```bash
pip install eling

# Run the Notion-only MCP server (online/remote memory, 5 tools)
python3 -m eling mcp

# Run the local-layers MCP server (facts, KB, code, builtin, HRR, 15+ tools)
python3 -m eling.as_brain.mcp_server
# or
eling as-brain

# Use the CLI
python3 -m eling --help

# If using OpenCode, install the lifecycle plugin:
eling-install-opencode
```

## 🔌 Agent Integration

| Agent | Integration | Status |
|-------|-------------|--------|
| **Hermes** | MCP server (Notion-only `eling` + local `as_brain`) + Memory Provider + Plugin | ✅ Tested |
| **OpenCode** | MCP server (both) + Lifecycle Plugin | ✅ Tested |
| **Zero** | MCP server (both) + Hooks + Skill | ✅ Bundled installer |

Non-tested agents connect via the stdio MCP servers — any MCP-compatible host can use both
`eling` (notion-only, 5 tools) and `as_brain` (local layers, 15+ tools).

### Hermes

Eling plugs into Hermes Agent at 3 levels:

**1. MCP Server** — add to `~/.hermes/config.yaml`:
```yaml
mcp_servers:
  eling:
    command: python3
    args: ["-m", "eling.mcp_server"]
    enabled: true
```

**2. Memory Provider** — sets default brain for `remember`/`recall`:
```yaml
memory:
  provider: eling
```

**3. Plugin** — registers `eling_remember` + `eling_recall` as quick tools:
```yaml
plugins:
  enabled:
    - eling
  eling:
    home: /root/.eling
```

### OpenCode

Eling provides an **OpenCode lifecycle plugin** that auto-writes session memory:

```bash
# After installing eling, run this to install the plugin:
eling-install-opencode

# Or:
python3 -m eling install-opencode
```

This copies `eling-memory.js` to OpenCode's plugin directory and registers it in `opencode.jsonc`. The plugin hooks into:

- **`chat.message`** — stores user prompts as facts
- **`tool.execute.after`** — stores tool observations as facts
- **`event` (session.idle / session.compacted)** — pushes high-trust facts to Notion

The eling MCP server should also be configured in OpenCode (`opencode.jsonc`):

```jsonc
"mcp": {
  "eling": {
    "type": "local",
    "command": ["python3", "-m", "eling.mcp_server"],
    "enabled": true
  }
}
```

### Zero

Eling provides a **one-command installer** for [Zero](https://github.com/Gitlawb/zero) terminal agent — hooks + skill + MCP in one go:

```bash
# After installing eling:
eling-install-zero
# Or:
python3 -m eling install-zero
# Preview without changes:
python3 -m eling install-zero --dry-run
```

This sets up:

| Component | What it does |
|-----------|-------------|
| **MCP Server** | Adds `eling` to Zero's `config.json` → all 22 tools available |
| **Hooks (4)** | Registers lifecycle hooks for auto-memory |
| **Skill** | Installs a `SKILL.md` that teaches Zero about eling's tools |

#### Hook mapping

| Zero Event | Eling action |
|------------|-------------|
| `sessionStart` | Warm caches, log session info |
| `beforeTool` | Recall relevant context for the tool |
| `afterTool` | Store file edits + tool results as facts |
| `sessionEnd` | Flush memory to disk, push to Notion |

#### Manual config

If you prefer to wire it up yourself, add the MCP server to Zero's `~/.config/zero/config.json`:

```json
"mcp": {
  "eling": {
    "command": "python3",
    "args": ["-m", "eling.mcp_server"]
  }
}
```

## 📋 CLI Commands

```bash
python3 -m eling remember   "I learned that..."
python3 -m eling recall     "what did I learn about X"
python3 -m eling probe      "X"
python3 -m eling reason     ["X", "Y"]
python3 -m eling reflect    1                 # promote fact_id 1 to Notion
python3 -m eling verify                        # query verification status
python3 -m eling verify-spec                   # run spec-kit conformance

# Memory version control (v0.5.1)
python3 -m eling snapshot  --reason "pre_evolution"  # snapshot facts DB
python3 -m eling list-snapshots                       # list all snapshots
python3 -m eling rollback  <snapshot_id>              # restore to snapshot

# Zettelkasten linking + evolution
python3 -m eling link-stats                    # link graph stats
python3 -m eling linked-facts 1                # facts linked to fact_id 1
python3 -m eling evolve                        # merge near-duplicate facts
python3 -m eling stats
python3 -m eling export     --format markdown
python3 -m eling sync       --direction push   # facts → Notion

# Agent integration
python3 -m eling install-opencode              # install OpenCode lifecycle plugin
python3 -m eling install-zero                  # install Zero hooks + skill + MCP
python3 -m eling init-rules                    # write steering rules for AI agents

# Temporal search (v0.6.0)
python3 -m eling search-temporal "last 3 days" --category testing
python3 -m eling search-temporal "kemarin"     # Indonesian language support

# Per-fact versioning (v0.6.0)
python3 -m eling versioned-update 1 "Updated content" --reason "correction"
python3 -m eling version-history 1
python3 -m eling undo-to-version 1 --version-id 0
python3 -m eling versioning-stats
```

## 🌐 Notion Setup (Tier 5)

Optional — skip this if you only need local memory.

1. **Create a Notion integration** at https://www.notion.so/my-integrations
   - Give it a name (e.g. "Eling Brain")
   - Copy the **Internal Integration Secret** (starts with `ntn_`)

2. **Share a parent page** with your integration
   - Open the page you want as your second brain root
   - Click **Share** → **Invite** → select your integration
   - Copy the page URL and extract the **page ID** (the UUID in the URL, e.g. `38f7b66e-c7e0-813f-85b0-d37cef59c1f7`)

3. **Set environment variables**:
```bash
export NOTION_API_KEY="ntn_..."
export NOTION_PARENT_PAGE_ID="38f7b66e-c7e0-813f-85b0-d37cef59c1f7"
```

### Note-taking behavior

Once configured, eling auto-creates a **📋 Task Logs** child page under your parent on first use:

```
📋 Hermes Vault (parent page — your configured root)
  ├── 📋 Task Logs        ← auto-created by eling
  │   ├── 💡 Eling test ← child pages from eling_reflect / remember(layer="notion")
  │   └── 💡 Another note
  ├── 🔑 API Keys...
  └── ...
```

Two ways to add notes to Notion:

| Method | Usage | Route |
|--------|-------|-------|
| `brain.reflect(fact_id)` / `eling_reflect` | Promote a high-trust fact to Notion | → auto-routes by category |
| `brain.remember("text", layer="notion")` / `eling_remember` with `layer=notion` | Store content directly as a Notion page | → auto-routes by category |

### Auto-routing by category

Content is automatically detected and routed to the right child page:

| Category | Triggers | Child page |
|----------|----------|-----------|
| `project_summary` | "project done/complete/selesai", "deploy success", "summary completion" | 🎯 Project Summaries |
| `credential` | "api_key", "password", "secret", "token", "credential" | 🔑 Credentials |
| `address` | "alamat", "address", "domicile", "tinggal di" | 📍 Addresses |
| `config` | "config", "setup", "setting", "environment" | ⚙️ Configurations |
| *(uncategorised)* | Everything else | 📋 Task Logs |

Example:
```python
# Auto-routes to 🎯 Project Summaries
b.remember("Project done, deployed to production", layer="notion")
# Auto-routes to 🔑 Credentials
b.remember("DATABASE_URL = postgres://...", layer="notion")
# Auto-routes to 📋 Task Logs (no pattern match)
b.remember("General note", layer="notion")
```

All child pages under these category pages are full Notion pages — you can edit, move, share, or reference them normally.

Or pass them explicitly in code:
```python
from eling.brain import Brain
b = Brain(
    notion_api_key="ntn_...",
    notion_parent_id="38f7b66e-..."
)
result = b.reflect(fact_id=1)
print(result)  # {"page_id": "...", "promoted": True}

# Or store directly as a note
result = b.remember("Quick note for Notion", layer="notion")
print(result)  # {"layer": "notion", "page_id": "...", ...}
```

> **Note**: `eling_reflect` and `remember(layer="notion")` check availability at call time and return a clear error if any config is missing — no silent failures.

## 🕒 Temporal Search & Per-Fact Versioning (v0.6.0)

Eling v0.6.0 introduces **time-aware fact retrieval** and **append-only per-fact versioning** — never lose a piece of knowledge again.

### Temporal Search

Query facts by time range using natural language — English or Indonesian:

```bash
python3 -m eling search-temporal "last 3 days"
python3 -m eling search-temporal "this week"
python3 -m eling search-temporal "kemarin"        # Indonesian: yesterday
python3 -m eling search-temporal "hari ini"       # today
```

**Supported patterns:**

| Language | Examples |
|----------|----------|
| 🇬🇧 English | `today`, `yesterday`, `this week`, `last month`, `last 3 days`, `last 7 days`, `last 30 days` |
| 🇮🇩 Indonesian | `hari ini`, `kemarin`, `minggu ini`, `bulan lalu`, `3 hari terakhir`, `7 hari terakhir`, `30 hari terakhir` |

**Python API:**

```python
from eling.brain import Brain
b = Brain()

# Temporal search - English
results = b.search_temporal("last 3 days", category="testing")

# Indonesian
results = b.search_temporal("kemarin")

# All facts in a time window
results = b.search_temporal("", since_days=7)
```

### Per-Fact Versioning

Every fact update is **append-only** — old versions are preserved in a `fact_versions` table:

```python
# Update a fact — previous content is versioned
result = b.versioned_update(1, "Newer content", reason="corrected typo")
# → {"fact_id": 1, "version_id": 2, "previous": "Old content", "new": "Newer content"}

# Get version history
history = b.get_version_history(1)
# → [{"version_id": 0, "content": "Original...", "changed_at": "...", "reason": "initial"},
#     {"version_id": 1, "content": "Updated...", "changed_at": "...", "reason": "corrected typo"}]

# Undo to a specific version (also versioned!)
result = b.undo_to_version(1, version_id=0)
# → {"fact_id": 1, "version_id": 3, "restored_from": 0}

# Versioning stats
stats = b.versioning_stats()
# → {"versioned_facts": 42, "total_versions": 156, "version_operations": 114}
```

**Available as MCP tools:** `eling_versioned_update`, `eling_get_version_history`, `eling_undo_to_version`, `eling_versioning_stats`.

## 🧠 Memory Version Control (v0.5.1)

Eling provides Git-like snapshot and rollback for your facts database:

```bash
# Before destructive ops, create a snapshot
python3 -m eling snapshot --reason "pre_evolution"

# List available snapshots
python3 -m eling list-snapshots

# Rollback to a previous state (auto-backups current DB first)
python3 -m eling rollback 20260703-120000-123
```

Snapshots are file-level copies managed via `snapshot.py`. Available as MCP tools: `eling_snapshot`, `eling_list_snapshots`, `eling_rollback`.

## 🎯 Steering Rules (v0.5.1)

Teach your AI agent **when** to use eling's MCP tools. Auto-detects Cursor, Claude Code, OpenCode, Kiro, and Gemini:

```bash
cd your-project
python3 -m eling init-rules
```

This writes:
- **Cursor**: `.cursor/rules/eling-memory-*.mdc`
- **Claude Code**: `.claude/rules/eling-memory-*.md`
- **OpenCode**: Appends to `AGENTS.md`
- **Generic**: `ELING_MEMORY.md` in project root

Rules cover: when to store/retrieve memories, session lifecycle, and memory hygiene.

## 🔍 Vector Embeddings (v0.5.1)

Optional semantic search via `sentence-transformers`:

```bash
pip install eling[embeddings]
# or
pip install eling[all]
```

Enable when creating a Brain or set `ELING_EMBEDDING_MODEL`:

```python
from eling.brain import Brain
b = Brain(embedding_model="all-MiniLM-L6-v2")
```

Hybrid search ranking: BM25 + Jaccard + HRR + **cosine similarity** from embeddings. Stored in a separate `fact_embeddings` table.

## 🛡️ Verify-on-Stop (Conditional + Universal)

Eling provides **verify-on-stop** nudges for AI agents that lack built-in
verification (e.g., OpenCode, OpenClaw, Cursor, Windsurf). When running under
Hermes, this feature automatically **skips** — because Hermes already has its
own `agent/verification_stop.py`.

### Universal mode — one shared brain for every agent

The `as_brain` MCP server can act as a **universal brain** for all connected
agents at once. Set `ELING_VERIFY_ALL_AGENTS=1` to force eling's
verify-on-stop to stay active for *every* agent — including Hermes — so the
shared brain provides verification regardless of harness. The default
(unset) keeps the original behaviour: Hermes skips eling's nudges and relies
on its own built-in verification.

```bash
# Run the shared brain with verification for ALL agents
ELING_VERIFY_ALL_AGENTS=1 python3 -m eling.as_brain.mcp_server
```

This powers multi-agent setups (e.g. Hermes + OpenCode + Zero sharing one
`as_brain` instance) where you want a single source of truth for
verification evidence.

### How it works

1. **Auto-detection** — Eling detects the host agent from the MCP client's
   `initialize` handshake (`clientInfo.name`), which is more reliable than
   environment variable heuristics (prevents false Hermes detection when
   OpenCode runs under Hermes)
2. **Agent auto-attribution** — The handshake client name becomes the default
   `source` for `brain_remember`, so each agent's memories are tagged with its
   own identity without manual configuration (override with an explicit
   `source` argument)
3. **File edit tracking** — When code files are edited via hooks or MCP tools,
   eling records them in a verification ledger
4. **Spec-kit conformance** — If the project has spec-kit artifacts
   (`specs/*/spec.md`), eling checks whether code changes cover each spec
   requirement and includes gaps in the nudge
5. **Verification nudge** — If code was edited but no passing tests/verification
   was recorded, eling produces a `[System: ...]` nudge message
6. **Recording** — Agents can call `brain_verify` MCP tool (as_brain server) to record verification
   results (`passed`, `failed`, `skipped`)

### Spec-kit Verification

Projects using [spec-kit](https://github.com/github/spec-kit) (Spec-Driven
Development) get automatic spec conformance checking:

- Eling detects `specs/<feature>/spec.md`, `plan.md`, and `tasks.md` artifacts
- Requirements are extracted from spec markdown and matched against code files
- The `eling_verify_spec` tool returns coverage stats + uncovered requirements
- The standard `eling_verify` tool includes spec-kit results when `spec_check=true`
- Uncovered requirements are listed in the verification nudge for the agent to address

### Usage via MCP

```json
// Query current status
{ "method": "tools/call", "params": { "name": "eling_verify", "arguments": {} } }

// Record a passing verification
{ "method": "tools/call", "params": {
    "name": "eling_verify",
    "arguments": { "status": "passed", "command": "pytest", "output": "364 passed" }
} }

// Run spec-kit conformance check
{ "method": "tools/call", "params": {
    "name": "eling_verify_spec",
    "arguments": { "changed_files": ["src/main.py"] }
} }

// Combine both: verify + spec-kit
{ "method": "tools/call", "params": {
    "name": "eling_verify",
    "arguments": { "spec_check": true }
} }
```

### Config

| Key | Default | Env | Description |
|-----|---------|-----|-------------|
| `verify_on_stop` | `true` | `ELING_VERIFY_ON_STOP` | Enable nudges for non-Hermes agents |
| `verify_on_stop_max_attempts` | `2` | `ELING_VERIFY_MAX_ATTEMPTS` | Max nudges per session |
| `adapter` | `hermes` | `ELING_ADAPTER` | Force adapter type |
| `home` | `$HERMES_HOME/eling` | `ELING_HOME` | Data directory for the universal brain (as_brain server) |
| `verify_all_agents` | `false` | `ELING_VERIFY_ALL_AGENTS` | Universal mode: provide verify-on-stop for ALL agents incl. Hermes |

```yaml
plugins:
  eling:
    adapter: auto       # auto-detect from env
    verify_on_stop: true
```

## 🏗️ Architecture

```
eling/
├── mcp_server.py          — JSON-RPC stdio server (Notion-only, 5 tools: eling_*)
├── as_brain/
│   └── mcp_server.py      — JSON-RPC stdio server (local brain, 20 tools: brain_*)
├── brain.py               — Orchestrator: routing + RRF fusion + sync + linking
├── config.py              — Layered config: env → json → defaults
├── hooks.py               — 15 lifecycle hooks + HookRegistry + evolution
├── verify_on_stop.py      — Verification ledger + nudge builder + spec-kit wiring
├── spec_kit.py            — Spec-kit artifact parser + coverage analyzer
├── privacy.py             — PII/secret stripping (19 patterns)
├── compress.py            — SHA-256 dedup + length compression
├── cli.py                 — CLI client (install-zero wires BOTH MCP servers)
├── fact_memory_provider.py — Standalone facts layer provider (no Brain dependency)
└── layers/
    ├── builtin.py    — Tier 1: Hermes MEMORY.md / USER.md loader
    ├── facts.py      — Tier 2: SQLite + HRR + BM25 + trust + linking + evolution
    ├── hrr.py        — Holographic Reduced Representations (optional numpy)
    ├── code.py       — Tier 3: CodeLayer wrapper
    ├── code_index.py — Pure-Python AST+regex code indexer
    ├── kb.py         — Tier 4: FTS5 + porter + trigram + RRF
    └── notion.py     — Tier 5: httpx Notion API client (lazy import)
```

## ⚡ Performance

- **Lazy imports** — numpy and httpx are imported only when their layer is first used, not at module load time
- `import eling` takes ~1.3s (was ~4.5s with module-level imports on Alpine)
- Pure-Python fallback when numpy unavailable (BM25-only retrieval still works)

## 📖 Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [Migration Guide](docs/MIGRATION.md)
- [Hooks Reference](docs/HOOKS.md)

## 🤝 Credits

- **HRR phase encoding + facts layer** — adapted from [holographic plugin](https://github.com/dusterbloom) by dusterbloom (Hermes PR #2351, MIT)
- **Spec-kit integration** — spec-driven development artifacts ([spec-kit](https://github.com/github/spec-kit) by GitHub, [MIT](https://github.com/github/spec-kit?tab=MIT-1-ov-file#readme))

## 📜 License

MIT © 2026 PatrickNoFilter

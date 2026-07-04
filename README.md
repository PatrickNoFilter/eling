<div align="center">

# 🧠 Eling

**Unified second brain for AI agents — 5-tier memory, HRR reasoning, 17 MCP tools, Zettelkasten linking, memory evolution, spec-kit verification, conditional verify-on-stop**

*"Eling" (Javanese): to remember, to be conscious, to be aware*

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-Ready-6366f1)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/eling)](https://pypi.org/project/eling/)

</div>

---

## ✨ What is Eling?

Eling is a **unified second brain** for AI agents. It merges 5 memory tiers into one MCP server — no external databases, no cloud services needed for local operation.

```
🧠 Tier 5: NOTION   — online brain, human-readable (optional)
📚 Tier 4: KB       — FTS5 knowledge corpus
🕸️ Tier 3: CODE     — codegraph symbol intelligence
💎 Tier 2: FACTS    — SQLite + HRR + BM25 hybrid with trust scoring
📌 Tier 1: BUILTIN   — Hermes MEMORY.md / USER.md
```

All accessible via **17 MCP tools** from a single stdio server:

| Tool | Purpose |
|------|---------|
| `eling_remember` | Store content — auto-routes to facts (short) or KB (long) |
| `eling_recall` | Cross-layer search with RRF fusion (BM25 + trigram + porter) |
| `eling_reason` | Compositional query tying multiple entities together |
| `eling_probe` | Get all facts about an entity |
| `eling_reflect` | Promote a high-trust fact to Notion as a permanent page |
| `eling_sync` | Bidirectional sync between memory layers |
| `eling_stats` | Show per-layer statistics |
| `eling_think` | Synthesis + gap analysis across layers |
| `eling_export` | Full brain export as JSON or Markdown |
| `eling_verify` | Query/record verification status with optional spec-kit check |
| `eling_verify_spec` | Run spec-kit conformance verification against project specs |
| `eling_link_stats` | Zettelkasten link graph statistics |
| `eling_linked_facts` | Get facts linked to a given fact_id |
| `eling_evolve` | Trigger memory evolution (merge near-duplicates) |
| `eling_snapshot` | Snapshot the facts database before destructive operations |
| `eling_list_snapshots` | List all available snapshots |
| `eling_rollback` | Rollback facts database to a named snapshot |

## 🚀 Quick Start

```bash
pip install eling

# Run MCP server (stdio — plug into any MCP host)
python3 -m eling.mcp_server

# Or use the CLI
python3 -m eling --help

# If using OpenCode, install the lifecycle plugin:
eling-install-opencode
```

## 🔌 Agent Integration

| Agent | Integration | Status |
|-------|-------------|--------|
| **Hermes** | MCP server + Memory Provider + Plugin | ✅ Tested |
| **OpenCode** | MCP server + Lifecycle Plugin | ✅ Tested |
| **Others** (OpenClaw, Cursor, Windsurf, Claude Code) | MCP server only | ⚠️ MCP only |

Non-tested agents connect exclusively via the stdio MCP server (`python3 -m eling.mcp_server`) — any MCP-compatible host can use all 17 tools.

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
python3 -m eling init-rules                    # write steering rules for AI agents
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

## 🛡️ Verify-on-Stop (Conditional)

Eling provides **verify-on-stop** nudges for AI agents that lack built-in
verification (e.g., OpenCode, OpenClaw, Cursor, Windsurf). When running under
Hermes, this feature automatically **skips** — because Hermes already has its
own `agent/verification_stop.py`.

### How it works

1. **Auto-detection** — Eling detects the host agent from the MCP client's
   `initialize` handshake (`clientInfo.name`), which is more reliable than
   environment variable heuristics (prevents false Hermes detection when
   OpenCode runs under Hermes)
2. **File edit tracking** — When code files are edited via hooks or MCP tools,
   eling records them in a verification ledger
3. **Spec-kit conformance** — If the project has spec-kit artifacts
   (`specs/*/spec.md`), eling checks whether code changes cover each spec
   requirement and includes gaps in the nudge
4. **Verification nudge** — If code was edited but no passing tests/verification
   was recorded, eling produces a `[System: ...]` nudge message
5. **Recording** — Agents can call `eling_verify` MCP tool to record verification
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

```yaml
plugins:
  eling:
    adapter: auto       # auto-detect from env
    verify_on_stop: true
```

## 🏗️ Architecture

```
eling/
├── mcp_server.py     — JSON-RPC stdio server (17 tools)
├── brain.py          — Orchestrator: routing + RRF fusion + sync + linking
├── config.py         — Layered config: env → json → defaults
├── hooks.py          — 15 lifecycle hooks + HookRegistry + evolution
├── verify_on_stop.py — Verification ledger + nudge builder + spec-kit wiring
├── spec_kit.py       — Spec-kit artifact parser + coverage analyzer
├── privacy.py        — PII/secret stripping (19 patterns)
├── compress.py       — SHA-256 dedup + length compression
├── cli.py            — CLI client for all 17 operations
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

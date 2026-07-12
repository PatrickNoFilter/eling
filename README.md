<div align="center">

# 🧠 Eling

**Lightweight memory, powerful retrieval — 6-tier second brain for AI agents**

Blackbox flight recorder · HRR reasoning · 4 MCP servers (57 tools) · Continuum Layer 7 multi-agent orchestration hub · temporal queries · per-fact versioning · vector search · Zettelkasten linking · memory evolution · spec-kit verification · conditional + universal verify-on-stop · ELING_HOME override · handshake agent auto-attribution · full-page retrieval (eling_get_page_full) · FactMemoryProvider

*"Eling" (Javanese): to remember, to be conscious, to be aware*

[![PyPI](https://img.shields.io/pypi/v/eling)](https://pypi.org/project/eling/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)

</div>

---

## ✨ What is Eling?

Eling is a **lightweight, unified second brain** for AI agents. It merges 6 memory tiers via four MCP servers — and adds a **Continuum Layer 7 orchestration tier** that turns eling into a shared hub for *multiple* AI coding agents. The **Blackbox Layer 2 flight recorder** captures all agent telemetry (file reads, edits, shell commands, tool calls) and scores them with 11 context-efficiency metrics — turning raw observability into actionable optimization suggestions. No external databases, no cloud services needed for local operation (though it optionally syncs to Notion for human readability).

Think of it as one memory stack that serves **both the agent and the human** — and one orchestration hub that serves **every agent you run**, with a flight recorder that watches everything they do:

```
📡 Layer 7: CONTINUUM — multi-agent orchestration hub (shared continuum.db, 15 continuum_* tools)
🧠 Layer 6: NOTION   — online brain, persistent, human-readable (optional)
📚 Layer 5: KB       — FTS5 knowledge corpus for long-form knowledge
🕸️ Layer 4: CODE     — codegraph symbol intelligence
💎 Layer 3: FACTS    — SQLite + HRR + BM25 hybrid with trust scoring
🔎 Layer 2: BLACKBOX — flight recorder + telemetry + 11-metric efficiency scoring
📌 Layer 1: BUILTIN  — Hermes MEMORY.md / USER.md (always-on prompt context)
```

### How the tiers work together

| Tier | What it stores | How it's queried | Persistence |
|------|---------------|-------------------|-------------|
| **📡 Continuum** | Dispatch registry, agent knowledge, PLOT protocol | `continuum_*` MCP tools | Local SQLite — shared across agents |
| **🧠 Notion** | Permanent pages, project plans, vault entries | `eling_reflect` promotes facts; `eling_sync push` syncs | Cloud — human-viewable, survives everything |
| **📚 KB** | Articles, docs, long-form knowledge chunks | FTS5 full-text search | Local SQLite — persistent |
| **🕸️ Code** | Function symbols, imports, class hierarchies | Codegraph traversal | Local SQLite — auto-indexed |
| **💎 Facts** | Short facts, preferences, observations | HRR + BM25 + trigram hybrid with trust scores | Local SQLite — append-only, versioned |
| **🔎 Blackbox** | Agent telemetry events, efficiency scores, baselines | `blackbox_*` MCP tools (watch/ingest/score) | Local SQLite — auto-recorded |
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

# Run the Notion-only MCP server (online/remote memory, 6 tools)
python3 -m eling mcp

# Run the local-layers MCP server (facts, KB, code, builtin, Blackbox, HRR — 33 tools)
python3 -m eling.as_brain.mcp_server
# or
eling as-brain

# Run the Blackbox flight recorder MCP (telemetry, scoring, baselines — 16 blackbox_* tools)
python3 -m eling blackbox mcp

# Run the Continuum Layer 7 orchestration hub (multi-agent, 15 continuum_* tools)
python3 -m eling continuum mcp
# or
eling-continuum

# Use the CLI
python3 -m eling --help

# If using OpenCode, install the lifecycle plugin:
eling-install-opencode
```

## 🔌 Agent Integration

| Agent | Integration | Status |
|-------|-------------|--------|
| **Hermes** | MCP (Notion `eling` + local `as_brain` + Blackbox `blackbox` + Continuum `continuum`) + Memory Provider + Plugin | ✅ Tested |
| **OpenCode** | MCP (all 3 servers) + Lifecycle Plugin | ✅ Tested |
| **MiMo-Code** | MCP (all 3 servers, OpenCode fork) | ✅ Tested |
| **Zero** | MCP (all 3 servers) + Hooks + Skill | ✅ Bundled installer |
| **Claude Code** | MCP (all 3 servers via `mcpServers`) | ✅ Wiring provided |
| **Codex** | MCP (all 3 servers via `mcp_servers`) | ✅ Wiring provided |

### One shared hub for every agent (Continuum Layer 7)

Continuum turns eling into a **single MCP hub** that all your coding agents connect
to. Each agent gets isolated git worktrees, a shared orchestration registry, and
two-tier knowledge (fundamental = binding rules, situational = semantic search) —
all routed through eling's memory. Every entry is auto-attributed by the agent's
MCP handshake name, so you always know which agent wrote what.

Wire all six agents in one command (Hermes, OpenCode, MiMo-Code, Zero, Claude Code, Codex):

```bash
# from the eling repo
chmod +x continuum/install.sh
continuum/install.sh --eling-home /shared/eling     # shared store for all agents
continuum/healthcheck.sh --eling-home /shared/eling # verify every agent is wired
```

Per-agent configs live in `continuum/configs/`; uninstall with `continuum/uninstall.sh`.
See **[`continuum/README.md`](continuum/README.md)** for the full guide.

Non-tested agents connect via the stdio MCP servers — any MCP-compatible host can use
`eling` (notion-only, 6 tools), `as_brain` (local layers, 33 tools), `blackbox`
(flight recorder, 16 `blackbox_*` tools), and `continuum` (orchestration hub, 15 `continuum_*` tools).

### 🔎 Blackbox Flight Recorder (Layer 2)

Blackbox is eling's **observability layer** — a real-time flight recorder that captures everything
your agents do and scores their efficiency. Think of it as a black-box data recorder for AI agents:
it logs every tool call, file read, file edit, shell command, and subagent spawn, then runs
11 context-efficiency metrics on the collected data.

**16 MCP tools** for telemetry capture and analysis:

| Tool | Purpose |
|------|---------|
| `blackbox_watch_start` / `stop` | Watch a Zero stream-JSON session in real-time |
| `blackbox_ingest` | Ingest telemetry events directly |
| `blackbox_ingest_zero_jsonl` | Import Zero stream-JSON log files |
| `blackbox_ingest_hermes_session` | Import a Hermes session from the state DB |
| `blackbox_runs_list` / `run_get` | List and inspect recorded runs |
| `blackbox_stats` | Aggregate statistics across runs |
| `blackbox_run_score` | 11-metric efficiency scoring |
| `blackbox_run_effectiveness` | Outcome scoring (did the task land?) |
| `blackbox_run_timeline` | Compact causal timeline of actions |
| `blackbox_run_suggest` | Optimization suggestions based on scores |
| `blackbox_run_handoff` | Export run summary for another agent |
| `blackbox_baselines_get` | Per-archetype baseline comparison |

**11 efficiency metrics** (ported from [Agent-Blackbox](https://github.com/nousresearch/agent-blackbox) by Taewoo Park):

| Metric | What it measures |
|--------|-----------------|
| Redundant reads | Files read twice without changes between |
| Cache hit ratio | Terminal output reuse vs. re-execution |
| Read amplification | Lines read per line written |
| Retry waste | Bash/compile failures retried |
| Yield density | Edits per tool call |
| Token efficiency | Total tokens used |
| Edit efficiency | Edits per file open |
| Test success | Passes per test run |
| Commit frequency | Commits per hour |
| Context window utilization | Proportion of context actually used |
| Subagent overhead | Orchestration cost of subagents |

**Agent support:**
- **Zero** — auto-captures telemetry via the eling hook plugin; watch live streams with `blackbox_watch_start`
- **Hermes** — import past sessions with `blackbox_ingest_hermes_session`; live capture via as_brain MCP
- **Any MCP agent** — ingest events directly via `blackbox_ingest`

The Blackbox recorder feeds its findings into the Facts layer (Layer 3) for persistent causal memory,
and baselines are stored per project across runs so you can track agent efficiency over time.

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

# Memory version control
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

## 🧠 Memory Version Control

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

## 🎯 Steering Rules

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

## 🔍 Vector Embeddings

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
├── mcp_server.py              — JSON-RPC stdio server (Notion-only, 5 tools: eling_*)
├── as_brain/
│   └── mcp_server.py          — JSON-RPC stdio server (local brain + Blackbox, 33 tools)
├── blackbox/                   — Layer 2: Flight recorder & telemetry
│   ├── core.py                — TraceEvent, RunSummary, AgentMetadata
│   ├── store.py               — SQLite-backed event store
│   ├── score.py               — 11-metric efficiency scoring engine
│   ├── effectiveness.py       — Outcome scoring
│   ├── timeline.py            — Causal timeline builder
│   ├── mcp_server.py          — 16 blackbox_* MCP tools
│   ├── cli.py                 — Blackbox CLI subcommands
│   └── adapters/
│       ├── zero.py            — Zero stream-JSON adapter + plugin
│       └── hermes.py          — Hermes session DB adapter
├── continuum/                  — Layer 7: Multi-agent orchestration hub
│   ├── mcp_server.py          — JSON-RPC stdio server (15 continuum_* tools)
│   ├── store.py               — continuum.db: projects, agents, knowledge, plot, reservations
│   ├── worktree.py            — Isolated per-agent git worktree manager
│   ├── plot.py                — PLOT.md canonical protocol (unified-diff mutations)
│   └── continuum.sh           — Shared wrapper exec'd by every agent's MCP config
├── brain.py               — Orchestrator: routing + RRF fusion + sync + snapshot
├── config.py              — Layered config: env → json → defaults
├── hooks.py               — 15 lifecycle hooks + HookRegistry
├── verify_on_stop.py      — Verification ledger + nudge builder + spec-kit wiring
├── spec_kit.py            — Spec-kit artifact parser + coverage analyzer
├── snapshot.py            — Git-like snapshot & rollback for facts DB
├── rules.py               — Steering rules generator (Cursor, Claude Code, OpenCode)
├── privacy.py             — PII/secret stripping (19 patterns)
├── compress.py            — SHA-256 dedup + length compression
├── cli.py                 — `eling` CLI (18 subcommands, includes blackbox dispatch)
├── fact_memory_provider.py — Standalone facts layer provider (no Brain)
├── opencode_plugin/       — Bundled OpenCode lifecycle plugin
│   └── eling-memory.js
└── layers/
    ├── builtin.py         — Layer 1: MEMORY.md / USER.md loader
    ├── facts.py           — Layer 3: SQLite + HRR + BM25 + Embeddings + Trust + Zettelkasten + Temporal + Versioning
    ├── embeddings.py      — Optional vector embeddings (Mistral API + sentence-transformers)
    ├── hrr.py             — Holographic Reduced Representations (numpy)
    ├── code.py            — Layer 4: CodeLayer wrapper
    ├── code_index.py      — Pure-Python AST+regex code indexer
    ├── kb.py              — Layer 5: FTS5 + porter + trigram + RRF
    └── notion.py          — Layer 6: httpx Notion API client
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
- **Blackbox flight recorder & efficiency scoring** — ported from [Agent-Blackbox](https://github.com/nousresearch/agent-blackbox) by [Taewoo Park](https://github.com/joint79) (Nous Research, MIT) — 11 context-efficiency metrics, causal timeline, per-archetype baselines
- **Continuum multi-agent orchestration** — inspired by [continuum](https://github.com/pouyahasanamreji/continuum) by [Pouya Hasanamreji](https://github.com/pouyahasanamreji) — worktree isolation, PLOT protocol, agent dispatch registry

## 📜 License

MIT © 2026 PatrickNoFilter

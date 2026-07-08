# v0.8.0 — Universal Brain: handshake agent attribution + ELING_HOME override + all-agents verify

The `as_brain` server becomes a **universal brain** for every connected agent.

## What's New

### Gap #1 — Auto agent source from MCP handshake
- The `as_brain` server captures `clientInfo.name` from the MCP `initialize`
  handshake and uses it as the default `source` for `brain_remember`.
- Each host agent's memories are auto-tagged with its own identity — no manual
  `source` configuration. An explicit `source` argument still overrides it.

### Gap #2 — ELING_HOME override (universal brain data dir)
- The `as_brain` server now resolves `ELING_HOME` explicitly and passes it to
  `Brain(home=...)`, making the data-directory override first-class and
  testable. Falls back to `$HERMES_HOME/eling` / `~/.eling` when unset.

### Gap #3 — Open verify-on-stop to all agents (universal mode)
- New env var `ELING_VERIFY_ALL_AGENTS=1` forces eling's verify-on-stop to stay
  active for **every** agent, including Hermes. Default (unset) preserves the
  original behaviour: Hermes skips eling's nudges and uses its built-in
  verification. Enables a single shared `as_brain` instance to verify all agents.

### Tests & Docs
- New `tests/test_universal_brain.py` (13 tests) covering all three gaps.
- README / API / ARCHITECTURE updated with universal-brain docs and the two new
  env vars.

---

# v0.7.3 — MCP Split: notion-only `eling` + local `as_brain` servers

**The single MCP server is split into two focused servers.**
`eling.mcp_server` is now notion-only (5 tools); the local layers (facts, KB,
code, builtin, HRR) moved to `eling.as_brain.mcp_server` (15+ tools) with
`brain_*` tool names.

## What's New

### MCP Server Split

- **`eling mcp`** → notion-only: `eling_remember`, `eling_search`,
  `eling_get_page`, `eling_create_page`, `eling_stats`.
- **`eling as-brain`** (new) → local layers: `brain_remember`, `brain_recall`,
  `brain_reason`, `brain_probe`, `brain_think`, `brain_stats`, `brain_export`,
  `brain_evolve`, `brain_snapshot` / `brain_list_snapshots` / `brain_rollback`,
  `brain_link_stats` / `brain_linked_facts`, `brain_search_temporal`,
  `brain_versioned_update` / `brain_get_version_history` / `brain_undo_to_version` /
  `brain_versioning_stats`, `brain_verify` / `brain_verify_spec`.
- Clean separation of concerns: online memory (Notion) vs. local memory layers.
- Tool names are now namespaced (`brain_*` for local, `eling_*` for Notion).

### CLI

- New `eling as-brain` command — starts the local-layers MCP server.
- `eling mcp` still starts the notion-only server (unchanged API).

### Zero Plugin Updates

- `SKILL.md` updated to document both MCP servers with separate tool tables.
- `eling-hook.py` updated to use the local brain for auto-memory.

### Documentation

- README, API.md, ARCHITECTURE.md updated for the MCP split.

# v0.7.2 — Memory provider release: FactMemoryProvider, lazy numpy, Hermes session flush

**FactMemoryProvider** is now bundled in the repo as a standalone memory provider
for the facts (1st) layer, with Hermes plugin integration and session-end flush.

## What's New

### FactMemoryProvider (standalone, no Brain dependency)

- New `eling.fact_memory_provider.FactMemoryProvider` class — wraps `FactsLayer`
  with a clean `remember`/`recall`/`forget`/`probe` interface.
- Privacy-first: PII redaction + SHA-256 dedup on every `remember` call.
- Hermes-compatible: 7 `fact_*` tools (`fact_remember`, `fact_recall`,
  `fact_forget`, `fact_probe`, `fact_stats`, `fact_evolve`, `fact_entity_neighbors`).
- Lazy init — underlying SQLite + HRR + BM25 created on first use.
- `close()` method for clean shutdown.

### Hermes Plugin Enhancements

- `hermes_plugin.py` registers **both** `eling_*` (full 5-layer Brain) and
  `fact_*` (standalone facts layer) tool groups.
- `on_session_end()` now flushes memory to disk and closes FactMemoryProvider.

### Termux/Legacy Compatibility

- `layers/embeddings.py`: numpy is now imported lazily via `_get_np()` — avoids
  import failures on systems without numpy (e.g. bare Termux). Falls back to
  `None` gracefully for all embedding operations.

### Documentation

- `__init__.py` exports `FactMemoryProvider` in `__all__`.

# v0.7.1 — Zero integration: install-zero command, hooks, skill, MCP

**Zero** (terminal coding agent) is now a first-class Eling integration target.

## What's New

### `eling install-zero` — One-Command Installer

A new CLI subcommand that installs Eling into Zero in one go:

```bash
pip install eling
eling-install-zero
# Or: python3 -m eling install-zero
```

Sets up **4 components**:

| Component | What it does |
|-----------|-------------|
| **MCP Server** | Registers Eling in Zero's `config.json` → all 22 tools as `mcp.eling.*` |
| **Hooks (4)** | Registers lifecycle hooks via `zero hooks add` — sessionStart, beforeTool, afterTool, sessionEnd |
| **Skill** | Installs `SKILL.md` to teach Zero about Eling's tools and usage patterns |
| **Hook script** | Copies `eling-hook.py` to `~/.zero/scripts/` |

### Auto-Memory via Zero Hooks

| Zero Event | Eling action |
|------------|-------------|
| `sessionStart` | Warm caches, log session info |
| `beforeTool` | Recall relevant context for the tool |
| `afterTool` | Store file edits + tool results as facts |
| `sessionEnd` | Flush memory to disk, push to Notion |

### Bundled Zero Plugin Files

- `src/eling/zero_plugin/SKILL.md` — Zero-optimized skill teaching Eling tool usage
- `src/eling/zero_plugin/eling-hook.py` — Hook script that reads JSON payload on stdin and dispatches to correct handler
- `src/eling/zero_plugin/__init__.py` — Package marker

### Documentation

- **README.md** — New "### Zero" section with one-command install, component table, hook mapping, manual config
- **docs/ZERO.md** — Full integration guide (install, MCP, hooks, skill, usage patterns, verification)
- **docs/API.md** — Added `install-zero` to CLI reference
- **docs/HOOKS.md** — Added Zero agent integration section

## Commits

```
fe81b49 docs: expand What is Eling? section, highlight Notion as online memory
... (all commits since v0.6.2)
```

# v0.1.0 — Unified second brain for AI agents

**Eling** (Javanese: *to remember, to be conscious, to be aware*) is a unified second brain for AI agents — 5-tier memory with HRR reasoning, forgetting engine, contradiction detection, self-wiring entity graph, and 9 MCP tools.

## Architecture

```
┬── Builtin  — agent identity, config, stats
├── Facts    — SQLite+HRR+BM25, entity-linked, strength-gated
├── KB       — FTS5 chunks, heading-based splitter
├── Code     — Pure Python AST+regex indexer, 15+ languages
└── Notion   — First-class native layer via MCP SDK
```

## 9 MCP Tools

| Tool | Purpose |
|------|---------|
| `eling_remember` | Store content in any layer (auto-routes) |
| `eling_recall` | Cross-layer search (BM25+HRR+Jaccard+RRF) |
| `eling_reason` | HRR compositional query across entities |
| `eling_probe` | All facts about a single entity |
| `eling_reflect` | Promote top local facts to Notion |
| `eling_forget` | Decay or delete facts (3-state lifecycle) |
| `eling_stats` | Layer statistics |
| `eling_think` | Synthesis + gap-analysis (recall+reason+report) |
| `eling_export` | Full JSON/markdown dump of all layers |

## Key Features

- **5-tier memory**: builtin/facts/KB/code/Notion in one unified server
- **HRR reasoning**: Vector-symbolic compositional queries (opt-in numpy)
- **Hybrid search**: BM25 + Jaccard + HRR + Reciprocal Rank Fusion
- **Forgetting engine**: Exponential decay, active/dormant/cleared 3-state lifecycle
- **Contradiction detection**: Jaccard-based similarity + tag flagging
- **Self-wiring entity graph**: [[entity]] extraction + co-occurrence edges
- **Privacy filter**: 19 pattern types, 48 tests
- **Harness adapters**: 5 platforms (Hermes, Claude Code, OpenCode, OpenClaw, OpenClaude)
- **Schema packs**: Config-driven default/coding/research schemas
- **Snapshot/rollback**: File-level DB snapshots before bulk ops
- **Permissions**: Declarative access control per source/layer
- **CI**: GitHub Actions matrix (3.10/3.11/3.12) + benchmark PR comments
- **384 tests** — stdlib only (opt-in numpy)

## Commits (16 total)

```
b3af2b3  feat: initial Eling — unified second brain for AI agents
db57ff5  test: add pytest suite with 86 tests across HRR, Facts, KB, Brain
22e73ba  feat: Phase 8–10 — privacy, hooks, config, sync, CLI, 229 tests
70988d2  embed(code): replace external codegraph CLI with pure-Python internal code indexer
b671b4c  docs: ARCHITECTURE.md, HOOKS.md, MIGRATION.md, API.md
b881c80  mcp: upgrade to universal shared brain (MCP + agent identity)
c548ba5  feat: add forgetting engine — decay.py + strength lifecycle + 3-state retention
81b8005  feat: self-wiring entity graph — [[entity]] extraction + co-occurrence edges
493e788  feat: contradiction/consistency check — Jaccard + tags + idle_30min sweep
d2a5eaa  feat: harness adapters (5 platforms) + schema packs (3 configs)
12e4cf4  feat: permissions enforcement — declarative access control
a53a297  feat: snapshot/rollback — file-level DB snapshots + auto backup
62d1ea2  feat: eling_think — 8th MCP tool, recall+reason+synthesis+gap-analysis
fd32218  feat: explicit export — JSON+markdown dump, 9th MCP tool, all 6 layers
1105858  feat: formal benchmark.py — per-layer p50/p95/p99, JSON output, 9 ops
5cd9fac  feat: GitHub Actions CI matrix (3.10/3.11/3.12) + benchmark + publish
```

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

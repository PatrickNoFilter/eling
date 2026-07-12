# Credits

Eling builds on ideas, patterns, and code from several open-source projects and research efforts.

## Core Architecture

- **Hermes Agent** (Nous Research) — memory/session/skill framework that inspired Eling's
  multi-layer cognitive architecture. Eling's Builtin Layer reads `MEMORY.md`/`USER.md`
  following Hermes conventions. [hermes-agent.ai](https://hermes-agent.ai)
- **Agent-Blackbox** (Nous Research / [Taewoo Park](https://github.com/joint79)) — Blackbox
  Layer 2 flight recorder is a port of the Agent-Blackbox concept: 11-metric
  context-efficiency scoring, SQLite event store, causal timeline builder, optimization
  suggestion engine, and per-archetype baselines. [MIT](https://github.com/nousresearch/agent-blackbox)
- **Continuum multi-agent orchestration** — worktree isolation, PLOT protocol, and agent
  dispatch registry inspired by [continuum](https://github.com/pouyahasanamreji/continuum)
  by [Pouya Hasanamreji](https://github.com/pouyahasanamreji).
- **Spec-kit integration** — spec-driven development artifacts from
  [spec-kit](https://github.com/github/spec-kit) by GitHub. [MIT](https://github.com/github/spec-kit?tab=MIT-1-ov-file#readme)
- **HRR phase encoding + facts layer** — adapted from
  [holographic plugin](https://github.com/dusterbloom) by dusterbloom
  (Hermes PR #2351, MIT).
- **httpx** — Notion Layer uses httpx directly (no subprocess MCP server), keeping
  the dependency lightweight.

## Memory & Retrieval

- **HRR** (Holographic Reduced Representations) — Plate and Churchland-style
  vector-symbolic architecture for compositional memory operations. Pure-Python
  numpy implementation.
- **BM25** — FTS5-based ranking with porter stemming and trigram fuzzy matching.
- **Zettelkasten** — Luhmann-inspired link-based memory evolution, adapted for
  AI agent context.

## Protocol & Extensions

- **MCP** (Model Context Protocol) — all Eling servers speak JSON-RPC over stdio
  following the Anthropic MCP specification.
- **Markdownify MCP** — document-to-Markdown conversion adapted from
  [markdownify-mcp](https://github.com/zcaceres/markdownify-mcp) by
  [Zach Caceres](https://zach.dev). Eling's implementation uses Microsoft's
  markitdown Python library natively (no Node.js dependency).
- **Zero stream-JSON** — Blackbox Zero adapter processes line-delimited JSON
  telemetry from Zero CLI agents.

## Tools That Helped Build Eling

- **Claude Code** (Anthropic) — used for feature development and code review.
- **OpenCode** (OpenAI) — used for testing and validation.
- **Hermes Agent** (Nous Research) — the agent building the agent.

## Maintainer

Eling is created and maintained by **PatrickNoFilter**.

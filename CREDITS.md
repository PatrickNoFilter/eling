# Credits

Eling builds on ideas, patterns, and code from several open-source projects and research efforts.

## Core Architecture

- **Hermes Agent** (Nous Research) — memory/session/skill framework that inspired Eling's
  multi-layer cognitive architecture. Eling's Builtin Layer reads `MEMORY.md`/`USER.md`
  following Hermes conventions. [hermes-agent.ai](https://hermes-agent.ai)
- **Agent-Blackbox** (Nous Research / Taewoo Park) — Blackbox Layer 2 flight recorder
  is a port of the Agent-Blackbox concept: 11-metric context-efficiency scoring,
  SQLite event store, causal timeline builder, and optimization suggestion engine.
- **httpx** — Notion Layer uses httpx directly (no subprocess MCP server), keeping
  the dependency lightweight.

## Memory & Retrieval

- **HRR** (Holographic Reduced Representations) — Platform and Churchland-style
  vector-symbolic architecture for compositional memory operations. Pure-Python
  numpy implementation.
- **BM25** — FTS5-based ranking with porter stemming and trigram fuzzy matching.
- **Zettelkasten** — Luhmann-inspired link-based memory evolution, adapted for
  AI agent context.

## Protocol & Extensions

- **MCP** (Model Context Protocol) — all Eling servers speak JSON-RPC over stdio
  following the Anthropic MCP specification.
- **Zero stream-JSON** — Blackbox Zero adapter processes line-delimited JSON
  telemetry from Zero CLI agents.
- **Continuum multi-agent orchestration** — inspired by scalable agent dispatch
  patterns from Claude Code, OpenCode, and Codex workflows.

## Tools That Helped Build Eling

- **Claude Code** (Anthropic) — used for feature development and code review.
- **OpenCode** (OpenAI) — used for testing and validation.
- **Hermes Agent** (Nous Research) — the agent building the agent.

## Maintainer

Eling is created and maintained by **PatrickNoFilter**.

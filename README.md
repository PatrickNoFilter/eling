<div align="center">

# 🧠 Eling

**Unified second brain for AI agents — Hermes-first**

*"Eling" (Javanese): to remember, to be conscious, to be aware*

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-Ready-6366f1)](https://modelcontextprotocol.io)
[![Hermes](https://img.shields.io/badge/Hermes-First-orange)](https://github.com/NousResearch/hermes-agent)

</div>

---

## ✨ What is Eling?

Eling is a **unified second brain** for AI agents. It merges 5 memory tiers into one MCP server:

```
🧠 Layer 5: NOTION (online brain, human-readable)
📚 Layer 4: KB (FTS5 knowledge corpus)
🕸️ Layer 3: CODE (codegraph symbol intelligence)
💎 Layer 2: FACTS (HRR + BM25 hybrid)
📌 Layer 1: BUILTIN (Hermes MEMORY.md/USER.md)
```

All accessible via **5 unified tools**:
- `eling_remember` — smart routing across layers
- `eling_recall` — cross-layer search with RRF fusion
- `eling_reason` — compositional query (multi-entity)
- `eling_reflect` — promote local fact → Notion
- `eling_sync` — bidirectional Notion ↔ local

## 🚀 Quick Start

```bash
pip install eling-memory

# As Hermes plugin
eling install-hermes-plugin

# As standalone MCP server
eling-mcp
```

## 🎯 Why Eling?

| Feature | Eling | mem0 | agentmemory | Mnemosyne |
|---------|-------|------|-------------|-----------|
| **Notion as online brain** | ✅ native | ❌ | ❌ | ❌ |
| **codegraph integration** | ✅ embedded | ❌ | ❌ | ❌ |
| **FTS5 knowledge base** | ✅ embedded | ❌ | ❌ | partial |
| **Hermes-first design** | ✅ | indirect | indirect | ✅ |
| **Single MCP process** | ✅ | ❌ | ❌ | ✅ |
| **HRR compositional reasoning** | ✅ | ❌ | ❌ | ✅ |
| **Indonesian-friendly** | ✅ | ❌ | ❌ | ❌ |

## 📖 Documentation

- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Migration from holographic](docs/migration.md)
- [Notion setup](docs/notion-setup.md)

## 🤝 Credits

- HRR phase encoding adapted from holographic plugin by [dusterbloom](https://github.com/dusterbloom) (Hermes PR #2351, MIT)
- FTS5 retrieval techniques inspired by Nous Research's [context-mode](https://github.com/NousResearch/context-mode)
- Architecture lessons from [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory) and [AxDSan/mnemosyne](https://github.com/AxDSan/mnemosyne)

## 📜 License

MIT © 2026 PatrickNoFilter

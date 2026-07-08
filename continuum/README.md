# Eling Continuum — multi-agent orchestration

One MCP hub. **Every** AI coding agent connects to it and shares the same memory
+ orchestration state. This adapts [Continuum](https://github.com/pouyahasanamreji/continuum)
into a pure-Python tier on top of [eling](https://github.com/PatrickNoFilter/eling)'s
5 memory tiers.

```
 agent A ─┐
 agent B ─┼─►  eling continuum MCP server  ─►  continuum.db (projects, knowledge, agents)
 agent C ─┤      (continuum.sh wrapper)         eling facts/KB/Notion (via ELING_HOME)
 agent D ─┘
```

## Install

```bash
git clone https://github.com/PatrickNoFilter/eling.git
cd eling
pip install -e .            # or: uv pip install -e .
chmod +x src/eling/continuum/continuum.sh
```

## Shared store (critical)

Every agent MUST point at the **same** `ELING_HOME` (where `continuum.db` lives),
otherwise each agent gets its own island. Pick one path and export it to all:

```bash
export ELING_HOME=/root/.eling     # or ~/.eling, your choice
```

## Wire up each agent

Copy the matching snippet from `configs/` into each agent's config:

| Agent | Config file | Snippet |
|-------|-------------|---------|
| **Hermes** | `~/.hermes/config.yaml` | `configs/hermes.yaml` → under `mcp_servers` |
| **OpenCode** | `~/.config/opencode/opencode.jsonc` | `configs/opencode.jsonc` → under `mcp` |
| **MiMo-Code** | `~/.config/opencode/opencode.jsonc` (MiMo fork reuses OpenCode config) | same as OpenCode |
| **Zero** | `~/.config/zero/config.json` | `configs/zero.config.json` → under `mcp.servers` |
| **Claude Code** | `~/.claude.json` (or project `.mcp.json`) | `configs/claude-code.json` → under `mcpServers` |
| **Codex** | `~/.codex/config.toml` | `configs/codex.toml` → under `[mcp_servers]` |

Claude Code / Codex / Cline also work — see `configs/claude-code.json` (Claude Code
`~/.claude.json` `mcpServers` block) and `configs/codex.toml` (Codex `~/.codex/config.toml`
`[mcp_servers]`). All four canonical agents + Claude Code + Codex now point at the
same hub.

## Verify a connection

```bash
# Hermes
hermes mcp test continuum

# Any agent — manual stdio handshake
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"probe","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n' \
  | timeout 5 /root/eling/src/eling/continuum/continuum.sh
```

## Agent source attribution

The orchestrator auto-tags every memory entry / agent registration with whichever
client connected (from the MCP `initialize` handshake `clientInfo.name`). So
`claude_code`, `codex`, `opencode`, `zero`, and `hermes` each show up as the
source — you always know *which* agent wrote what.

## The continuum_* tools

- `continuum_project_create` / `project_get` / `project_list`
- `continuum_knowledge_create` / `knowledge_get` / `knowledge_list` / `knowledge_search`
  (two-tier: `kind="fundamental"` = binding rules loaded every dispatch;
   `kind="situational"` = semantic/BM25 search)
- `continuum_agent_register` / `agent_update` / `agent_get` / `registry_list`
  (state machine `draft→active→merged|abandoned`; `merged` needs a 7–40 char SHA)
- `continuum_plot_get` / `plot_update` (PLOT.md, mutated via unified diff)
- `continuum_dispatch` (register + create isolated git worktree + return a ready prompt)
- `continuum_reservations` (reserved_path collision check across active agents)

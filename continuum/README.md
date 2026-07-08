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

## One-command install (all agents)

`continuum/install.sh` patches every supported agent's config in place with a
`continuum` MCP entry (all sharing your `ELING_HOME`), backing each file up to
`<file>.bak-continuum` first. It is idempotent (re-running skips already-patched
files) and merges into existing configs without clobbering other servers.

```bash
# from inside the eling repo
chmod +x continuum/install.sh

continuum/install.sh                          # auto-detect path, ELING_HOME=~/.eling
continuum/install.sh --eling-home /data/store # shared store for all agents
continuum/install.sh --eling-path /opt/eling  # if eling lives elsewhere
continuum/install.sh --agents hermes,zero     # limit to specific agents
continuum/install.sh --dry-run                # print actions, change nothing
continuum/install.sh --force                  # re-patch ignoring markers
```

Supported agents: `hermes`, `opencode` (+ MiMo-Code, same file), `zero`,
`claude-code`, `codex`. After running, restart or reload each agent
(Hermes: `/reload-mcp`).

## One-command uninstall

`continuum/uninstall.sh` removes the `continuum` MCP entry from every agent. If a
`<file>.bak-continuum` backup exists (from `install.sh`), it restores the original
file; otherwise it strips the continuum block/key in place.

```bash
continuum/uninstall.sh                 # all agents
continuum/uninstall.sh --agents hermes # limit scope
continuum/uninstall.sh --dry-run       # print actions, change nothing
continuum/uninstall.sh --keep-backups  # restore but keep .bak-continuum files
```

Restart/reload each agent afterwards to drop the continuum tools.



```bash
# Hermes
hermes mcp test continuum

# Any agent — manual stdio handshake
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"probe","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n' \
  | timeout 5 /root/eling/src/eling/continuum/continuum.sh
```

## Manual configs (if you prefer not to use install.sh)

Per-agent snippets live in `continuum/configs/`:

| Agent | File | Target |
|-------|------|--------|
| Hermes | `hermes.yaml` | `~/.hermes/config.yaml` (under `mcp_servers`) |
| OpenCode / MiMo-Code | `opencode.jsonc` | `~/.config/opencode/opencode.jsonc` (under `mcp`) |
| Zero | `zero.config.json` | `~/.config/zero/config.json` (under `mcp.servers`) |
| Claude Code | `claude-code.json` | `~/.claude.json` (under `mcpServers`) |
| Codex | `codex.toml` | `~/.codex/config.toml` (under `[mcp_servers]`) |

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

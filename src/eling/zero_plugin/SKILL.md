# Eling Memory System (MCP Split)

Eling has been split into **two MCP servers**:

| MCP Server | Config Key | Purpose |
|------------|-----------|---------|
| `eling` | `mcp.eling` | Notion-only — remote/online second brain |
| `as_brain` | `mcp.as_brain` | Local memory — facts, KB, code, builtin, HRR |

## Configuration

In `~/.config/zero/config.json`:

```json
"mcp": {
  "eling": {
    "command": "eling-termux-mcp",
    "description": "Notion-based second brain (remote/online memory)"
  },
  "as_brain": {
    "command": "as-brain-mcp",
    "description": "Local memory layers: facts, KB, code, builtin, HRR"
  }
}
```

## Available Tools

### `as_brain` (local layers) — `mcp.as_brain.*`

| Tool | Purpose |
|------|---------|
| `brain_remember` | Store content → facts/KB |
| `brain_recall` | Search local layers (RRF fusion) |
| `brain_reason` | Find facts connecting entities (HRR) |
| `brain_probe` | Get facts about an entity |
| `brain_think` | Synthesis + gap analysis |
| `brain_stats` | Memory health stats |
| `brain_export` | Export all layers as JSON/Markdown |
| `brain_evolve` | Merge near-duplicate facts |
| `brain_snapshot` / `brain_list_snapshots` / `brain_rollback` | Fact DB snapshots |
| `brain_link_stats` / `brain_linked_facts` | Zettelkasten link graph |
| `brain_search_temporal` | Temporal query |
| `brain_versioned_update` / `brain_get_version_history` / `brain_undo_to_version` / `brain_versioning_stats` | Per-fact versioning |
| `brain_verify` / `brain_verify_spec` | Verify-on-stop |

### `eling` (Notion only) — `mcp.eling.*`

| Tool | Purpose |
|------|---------|
| `eling_remember` | Store content as a Notion page |
| `eling_search` | Search Notion pages by title |
| `eling_get_page` | Fetch a Notion page as markdown |
| `eling_create_page` | Create a new Notion page |
| `eling_stats` | Check Notion connection status |

## Auto-Memory Hooks

Zero lifecycle hooks auto-store file edits and tool results into the local brain (not Notion). These fire automatically using the Python API (not MCP):

| Event | What happens |
|-------|-------------|
| `sessionStart` | Warm caches, log session info |
| `beforeTool` | Recall relevant context for the tool |
| `afterTool` | Store file edits + tool results as facts |
| `sessionEnd` | Flush memory to disk, optionally push to Notion |

## Usage Patterns

1. **Store local context**: `mcp.as_brain.brain_remember` with `source='zero'`
2. **Recall prior context**: `mcp.as_brain.brain_recall` with relevant query
3. **Push to Notion**: `mcp.eling.eling_remember` (stores directly as a page)
4. **Check health**: `mcp.as_brain.brain_stats` for local, `mcp.eling.eling_stats` for Notion
5. **End of feature**: `mcp.as_brain.brain_snapshot` before bulk ops

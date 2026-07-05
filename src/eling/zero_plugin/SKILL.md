# Eling Memory System

Eling is a **5-layer memory system** (facts + KB + code + Notion + HRR) running as an MCP server inside Zero.

## Configuration

Eling MCP server is pre-configured in Zero's `~/.config/zero/config.json`:

```json
"mcp": {
  "eling": {
    "command": "python3",
    "args": ["-m", "eling.mcp_server"]
  }
}
```

## Available MCP Tools

### Store & Retrieve
- `mcp.eling.eling_remember` — Store content. Short → facts layer, long → KB. Pass `source='zero'` to tag origin.
- `mcp.eling.eling_recall` — Cross-layer search with RRF fusion. Use this when you need prior context.
- `mcp.eling.eling_probe` — Get all facts about an entity/name.
- `mcp.eling.eling_reason` — Find facts connecting MULTIPLE entities.

### Analysis
- `mcp.eling.eling_think` — Synthesis + gap-analysis: checks stale/contradicted/unknown facts.
- `mcp.eling.eling_stats` — Memory health statistics.
- `mcp.eling.eling_link_stats` — Fact link graph stats.

### Sync & Export
- `mcp.eling.eling_sync` — Push facts to disk/Notion.
- `mcp.eling.eling_export` — Export all layers as JSON/Markdown.

### Maintenance
- `mcp.eling.eling_evolve` — Merge near-duplicate facts.
- `mcp.eling.eling_snapshot` — Snapshot before destructive ops.
- `mcp.eling.eling_rollback` — Restore from snapshot.

## Auto-Memory Hooks

Zero lifecycle hooks auto-store file edits and tool results into Eling. These fire automatically:

| Event | What happens |
|-------|-------------|
| `sessionStart` | Warm caches, log session info |
| `beforeTool` | Recall relevant context for the tool |
| `afterTool` | Store file edits + tool results as facts |
| `sessionEnd` | Flush memory to disk, push to Notion |

## Usage Patterns

1. **Before answering from memory**: `mcp.eling.eling_recall` with relevant query
2. **After learning preferences**: `mcp.eling.eling_remember` with `category=user_pref`
3. **When exploring code**: `mcp.eling.eling_remember` with `layer=kb` for docs
4. **End of feature**: `mcp.eling.eling_sync` to persist, `mcp.eling.eling_stats` to verify

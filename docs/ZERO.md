# Eling + Zero Integration

[Zero](https://github.com/Gitlawb/zero) is a terminal coding agent. Eling integrates with
Zero at **three levels**: MCP server, lifecycle hooks, and skill.

## Quick Install

```bash
pip install eling
eling-install-zero
# Or:
python3 -m eling install-zero
```

This single command:

1. **Copies the hook script** to `~/.zero/scripts/eling-hook.py`
2. **Registers 4 hooks** via `zero hooks add`
3. **Installs the eling skill** to `~/.local/share/zero/skills/eling/SKILL.md`
4. **Adds the MCP server** to `~/.config/zero/config.json`

## What You Get

### MCP Server — 22 Memory Tools

All 22 Eling tools available as `mcp.eling.*` in Zero — store, recall, reason,
sync, version, snapshot, and more.

### Hooks — Automatic Memory

| Hook | When | What Eling Does |
|------|------|----------------|
| `sessionStart` | Session starts | Warm caches, show memory stats |
| `beforeTool` | Before any tool | Recall relevant facts for the tool |
| `afterTool` | After any tool | Store file edits + results as facts |
| `sessionEnd` | Session ends | Flush to disk, push to Notion |

### Skill — Zero Knows About Eling

The skill teaches Zero to use `eling_remember`, `eling_recall`,
`eling_probe`, `eling_reason`, `eling_think`, and more proactively.

## Manual Configuration

### MCP Server

Add to `~/.config/zero/config.json`:

```json
"mcp": {
  "eling": {
    "command": "python3",
    "args": ["-m", "eling.mcp_server"]
  }
}
```

### Hooks

```bash
zero hooks add eling-sessionstart --event sessionStart \
    --command 'python3 ~/.zero/scripts/eling-hook.py'
zero hooks add eling-beforetool --event beforeTool \
    --command 'python3 ~/.zero/scripts/eling-hook.py'
zero hooks add eling-aftool --event afterTool \
    --command 'python3 ~/.zero/scripts/eling-hook.py'
zero hooks add eling-sessionend --event sessionEnd \
    --command 'python3 ~/.zero/scripts/eling-hook.py'
```

### Skill

Copy `SKILL.md` to `~/.local/share/zero/skills/eling/SKILL.md`.

## Usage Patterns

### In a Zero session

The agent will automatically:

- **Remember** every file edit and tool result
- **Recall** relevant past context before running tools
- **Flush** memory at session end

You can also prompt Zero explicitly:

> "Recall what we learned about the API design"
> "Store this user preference: prefers concise responses"
> "Check memory stats to see how many facts we have"

## Verification

```bash
# Check hooks are installed
zero hooks list

# Check skill is installed
zero skills list

# Check MCP server is registered
cat ~/.config/zero/config.json | grep eling
```

# Eling Hooks — 15 Lifecycle Hooks

Eling registers **15 lifecycle hooks** that fire at key moments during agent interaction. Hooks enable automatic memory management — indexing user input, recalling relevant facts, storing tool observations, promoting decisions to Notion, and more.

---

## Hook Architecture

```
HookRegistry (thread-safe)
  ├── register(name, handler)    — Add a handler to a hook
  ├── unregister(name, handler)  — Remove a handler
  ├── fire(name, context)        — Execute all handlers, catch exceptions
  └── total_handlers             — Total registered handlers count
```

- Each hook can have **multiple handlers**; all fire in registration order
- One handler **cannot crash another** — exceptions are caught and logged
- Handler signature: `(hook_name: str, context: dict) -> Any`
- Return values collected in a list and returned by `fire()`

---

## Hook Lifecycle Order

```
SESSION START
    │
    ├─ session_start
    │
    ├─── USER MESSAGE ────
    │   ├─ pre_user_message
    │   ├─ post_user_message
    │   │
    │   ├─── TOOL USE ────
    │   │   ├─ pre_tool_use
    │   │   ├─ post_tool_use
    │   │   └
    │   │
    │   └─ post_assistant_message
    │
    ├─── EVENTS ────
    │   ├─ decision_made    (user correction/affirmation)
    │   ├─ file_edit        (file modification detected)
    │   ├─ error_occurred   (tool/agent error)
    │   └─ compaction       (context window compaction)
    │
    ├─── SYNC ────
    │   ├─ sync_start
    │   ├─ sync_complete
    │   └─ sync_error
    │
    ├─ idle_30min       (30 min inactivity → background reflect)
    │
    └─ session_end
```

---

## Hook Reference

### 1. `session_start`
| | |
|---|---|
| **Fires** | When Brain is initialized / Hermes session begins |
| **Default action** | Warm caches, load project profile |
| **Context** | `{}` |
| **Returns** | `{facts_count, kb_sources, code_available, notion_available, top_concepts[]}` |

**Example:**
```python
brain.fire_hook("session_start")
# → {"facts_count": 42, "kb_sources": 5, "code_available": true, ...}
```

---

### 2. `pre_user_message`
| | |
|---|---|
| **Fires** | Before processing user input |
| **Default action** | Search facts + KB for relevant memories matching user message |
| **Context** | `{content: str, source: str}` |
| **Returns** | `{injected: bool, memories: list}` |

**Example output:**
```json
{
  "injected": true,
  "memories": [
    {"content": "User prefers concise responses", "_layer": "facts"},
    {"content": "Project uses pytest with xdist", "_layer": "kb"}
  ]
}
```

---

### 3. `post_user_message`
| | |
|---|---|
| **Fires** | After user input is indexed |
| **Default action** | Store user prompt as a fact (category: "user_prompt") |
| **Context** | `{content: str, source: str}` |
| **Returns** | `{indexed: bool, fact_id: int}` |

---

### 4. `pre_tool_use`
| | |
|---|---|
| **Fires** | Before a tool call executes |
| **Default action** | Recall context relevant to the tool name and arguments |
| **Context** | `{tool_name: str, arguments: any}` |
| **Returns** | `{recalled: bool, results: list}` |

---

### 5. `post_tool_use`
| | |
|---|---|
| **Fires** | After a tool call returns |
| **Default action** | Store tool observation as a fact (category: "tool_observation") |
| **Context** | `{tool_name: str, result: any}` |
| **Returns** | `{stored: bool, fact_id: int}` |

---

### 6. `post_assistant_message`
| | |
|---|---|
| **Fires** | After assistant reply is generated |
| **Default action** | Store assistant reply as a fact (category: "assistant_reply") |
| **Context** | `{content: str}` |
| **Returns** | `{facts_stored: int, fact_id: int}` |

---

### 7. `decision_made`
| | |
|---|---|
| **Fires** | User correction, affirmation, or decision |
| **Default action** | Index correction at high trust (0.95) or decision at 0.9 |
| **Context** | `{content: str, correction?: str}` |
| **Returns** | `{corrected: bool, fact_id: int}` or `{decided: bool, fact_id: int}` |

**Usage:**
```python
# User corrects a previous statement
brain.fire_hook("decision_made", {
    "correction": "The build system is hatchling, not setuptools"
})
# → fact stored at trust 0.95
```

---

### 8. `file_edit`
| | |
|---|---|
| **Fires** | File modification detected (by code layer watcher) |
| **Default action** | Re-index the file in CodeIndex |
| **Context** | `{file_path: str}` |
| **Returns** | `{reindexed: bool, file: str}` |

---

### 9. `error_occurred`
| | |
|---|---|
| **Fires** | Tool or agent error |
| **Default action** | Store error + context as a fact (category: "error") |
| **Context** | `{error: str, tool_name: str, context?: str}` |
| **Returns** | `{stored: bool, fact_id: int}` |

---

### 10. `compaction`
| | |
|---|---|
| **Fires** | Context window compaction |
| **Default action** | Snapshot session highlights as a fact (category: "session_summary") |
| **Context** | `{summary: str}` |
| **Returns** | `{stored: bool, fact_id: int}` |

---

### 11. `session_end`
| | |
|---|---|
| **Fires** | Session ends |
| **Default action** | Write summary to Notion (if available) → fallback to facts |
| **Context** | `{summary: str}` |
| **Returns** | `{notion_page: str}` or `{stored: bool, fact_id: int}` |

---

### 12. `idle_30min`
| | |
|---|---|
| **Fires** | 30 minutes of inactivity |
| **Default action** | Snapshot → apply decay → contradiction sweep → **memory evolution** (merge near-duplicates) → promote high-trust (≥0.9) facts to Notion |
| **Context** | `{notion_parent_id?: str}` |
| **Returns** | `{snapshot, promoted, decay, contradictions, evolved}` |

---

### 13. `sync_start`
| | |
|---|---|
| **Fires** | Sync operation begins |
| **Default action** | No-op (log only) |
| **Context** | `{direction: str, layer: str}` |
| **Returns** | `{handled: false}` |

---

### 14. `sync_complete`
| | |
|---|---|
| **Fires** | Sync operation completes successfully |
| **Default action** | No-op (log only) |
| **Context** | `{direction: str, layer: str, result: dict}` |
| **Returns** | `{handled: false}` |

---

### 15. `sync_error`
| | |
|---|---|
| **Fires** | Sync operation fails |
| **Default action** | No-op (log only) |
| **Context** | `{direction: str, layer: str, error: str}` |
| **Returns** | `{handled: false}` |

---

## Custom Handlers

### Register
```python
from eling.hooks import HOOK_POST_ASSISTANT_MESSAGE

def my_handler(name: str, ctx: dict) -> dict:
    content = ctx.get("content", "")
    # custom logic...
    return {"custom": True, "length": len(content)}

brain.hooks.register(HOOK_POST_ASSISTANT_MESSAGE, my_handler)
```

### Unregister
```python
brain.hooks.unregister(HOOK_POST_ASSISTANT_MESSAGE, my_handler)
```

### Fire manually
```python
results = brain.fire_hook("decision_made", {
    "content": "Always use pytest for testing"
})
```

### Reset all handlers
```python
brain.hooks.reset()  # testing only
```

---

## Disabling Hooks

No built-in mechanism to disable individual hooks at runtime. To skip a hook's
default behavior, register your own handler and ignore the default one, or
set the relevant feature flag in config (e.g. `notion_enabled: false` disables
Notion-dependent hooks).

For development/testing, call `brain.hooks.reset()` and register only what you need.

---

## Zero Agent Integration

Eling hooks map to [Zero](https://github.com/Gitlawb/zero) lifecycle events when installed
via `python3 -m eling install-zero`:

| Eling Hook | Zero Event | Purpose |
|------------|------------|---------|
| `session_start` | `sessionStart` | Warm caches, log session info |
| `pre_tool_use` | `beforeTool` | Recall context relevant to the tool about to run |
| `post_tool_use` | `afterTool` | Store tool results and file edits as facts |
| `session_end` | `sessionEnd` | Flush memory to disk, push to Notion |

The Zero hook script (`eling-hook.py`) reads JSON payload on stdin and dispatches
to the correct handler. It is installed automatically by `eling install-zero`.

### How Zero triggers Eling hooks

```
Zero sessionStart ──► eling-hook.py sessionStart ──► brain: warm caches
Zero beforeTool   ──► eling-hook.py beforeTool   ──► brain.recall(query)
Zero afterTool    ──► eling-hook.py afterTool    ──► brain.remember(file_edit)
Zero sessionEnd   ──► eling-hook.py sessionEnd   ──► brain.sync(flush) + notion push
```

For manual hook registration:

```bash
zero hooks add eling-sessionstart --event sessionStart \
    --command 'python3 ~/.zero/scripts/eling-hook.py'
```

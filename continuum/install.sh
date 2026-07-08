#!/bin/sh
# install.sh — wire every AI agent into the Eling Continuum orchestration hub.
#
# For each agent it patches the config IN PLACE with a `continuum` MCP server
# entry pointing at src/eling/continuum/continuum.sh, all sharing one ELING_HOME.
# Every file is backed up to <file>.bak-continuum before editing, and the
# operation is idempotent (re-running only touches files not yet patched).
#
# Usage:
#   continuum/install.sh                 # auto-detect eling path, ELING_HOME=~/.eling
#   continuum/install.sh --eling-home /data/store
#   continuum/install.sh --eling-path /opt/eling
#   continuum/install.sh --agents hermes,zero,claude-code   # limit scope
#   continuum/install.sh --dry-run       # print actions, write nothing
#   continuum/install.sh --force         # re-patch even if marker already present
#
# Supported agents: hermes, opencode (+mimo), zero, claude-code, codex

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEFAULT_WRAPPER="$REPO_ROOT/src/eling/continuum/continuum.sh"

ELING_PATH="$DEFAULT_WRAPPER"
ELING_HOME="$HOME/.eling"
AGENTS=""
DRY_RUN=0
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --eling-path)  ELING_PATH="$2"; shift 2 ;;
    --eling-home)  ELING_HOME="$2"; shift 2 ;;
    --agents)      AGENTS="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --force)       FORCE=1; shift ;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# Resolve wrapper to an absolute path (agents need an absolute command).
case "$ELING_PATH" in
  /*) WRAPPER="$ELING_PATH" ;;
  *)  WRAPPER="$(cd -- "$ELING_PATH" 2>/dev/null && pwd)/continuum.sh" ;;
esac
# If --eling-path pointed at the repo root, append the known subpath.
case "$WRAPPER" in
  */continuum.sh) : ;;
  *) WRAPPER="$WRAPPER/src/eling/continuum/continuum.sh" ;;
esac

if [ ! -f "$WRAPPER" ]; then
  echo "ERROR: continuum.sh not found at: $WRAPPER" >&2
  echo "Pass --eling-path to the eling repo, or run from inside the repo." >&2
  exit 1
fi

# chmod +x the wrapper so agents can exec it.
if [ "$DRY_RUN" -eq 0 ]; then
  chmod +x "$WRAPPER"
fi

want() {
  # Returns 0 if agent $1 should be processed (all, or in --agents list).
  [ -z "$AGENTS" ] && return 0
  echo ",$AGENTS," | grep -q ",$1,"
}

# ---- embedded Python patcher (stdlib only; handles json/jsonc/toml/yaml) ----
patch() {
  # $1 format  $2 target  $3 key-path-or-''  $4 block-json-or-''  (TOML/YAML use WRAPPER/ELING_HOME)
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] would patch ($1): $2"
    return 0
  fi
  python3 - "$1" "$2" "$WRAPPER" "$ELING_HOME" "$3" "$4" "$FORCE" <<'PYEOF'
import sys, json, os, re, shutil, tomllib

fmt, target, wrapper, eling_home, keypath, block_json, force = sys.argv[1:8]
force = force == "1"
keypath = keypath or ""
block = json.loads(block_json) if block_json else None

def backup(p):
    if os.path.exists(p) and os.path.getsize(p):
        shutil.copy2(p, p + ".bak-continuum")

def mkdir_for(p):
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)

MARKER = "# >>> eling-continuum >>>"

def strip_jsonc(s):
    out = []
    for line in s.split("\n"):
        res, instr, esc = "", False, False
        j = 0
        while j < len(line):
            c = line[j]
            if esc:
                res += c; esc = False; j += 1; continue
            if c == "\\":
                res += c; esc = True; j += 1; continue
            if c == '"':
                instr = not instr; res += c; j += 1; continue
            if not instr and c == "/" and j + 1 < len(line) and line[j+1] == "/":
                break
            res += c; j += 1
        out.append(res)
    return "\n".join(out)

def patch_json(target, keypath, block):
    backup(target)
    data = {}
    if os.path.exists(target) and os.path.getsize(target):
        data = json.load(open(target))
    # Idempotency: skip if the target key already exists.
    cur = data
    leaf = keypath.split(".")[-1]
    for key in keypath.split(".")[:-1]:
        cur = cur.get(key, {})
        if not isinstance(cur, dict):
            break
    if isinstance(cur, dict) and leaf in cur:
        print("  already patched (key present):", target); return
    cur = data
    for key in keypath.split(".")[:-1]:
        cur = cur.setdefault(key, {})
    cur[keypath.split(".")[-1]] = block
    mkdir_for(target)
    json.dump(data, open(target, "w"), indent=2)
    print("  patched (json):", target)
def patch_toml(target):
    backup(target)
    content = open(target).read() if (os.path.exists(target) and os.path.getsize(target)) else ""
    if (MARKER in content) and not force:
        print("  already patched (marker):", target); return
    if "mcp_servers.continuum" in content.replace(" ", ""):
        print("  WARN mcp_servers.continuum already present in", target, "- edit manually"); return
    mkdir_for(target)
    block = ('\n' + MARKER + '\n'
             '[mcp_servers.continuum]\n'
             f'command = "{wrapper}"\n'
             'args = []\n'
             f'env = {{ ELING_HOME = "{eling_home}" }}\n'
             '# <<< eling-continuum <<<\n')
    with open(target, "a") as f:
        f.write(block)
    print("  appended (toml):", target)


def patch_jsonc(target, keypath, block):
    backup(target)
    raw = open(target).read() if (os.path.exists(target) and os.path.getsize(target)) else ""
    if (MARKER in raw) and not force:
        print("  already patched (marker):", target); return
    block_json = json.dumps(block)
    # Brace-aware insert into the existing "mcp" object (preserves comments + siblings).
    m = re.search(r'"mcp"\s*:\s*\{', raw)
    if raw.strip() and m:
        start = m.end() - 1  # index of the '{'
        depth = 0
        end = start
        for pos in range(start, len(raw)):
            if raw[pos] == "{":
                depth += 1
            elif raw[pos] == "}":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        mcp_block = raw[start : end + 1]
        if re.search(r'"continuum"\s*:', mcp_block):
            print("  already patched (key present):", target); return
        insertion = ' "continuum": ' + block_json + ','
        new = raw[: start + 1] + insertion + raw[start + 1 :]
        mkdir_for(target)
        open(target, "w").write(new)
        print("  patched (jsonc, comments preserved):", target)
        return
    # No mcp object yet -> parse (comments dropped only in this rare path) and emit.
    cleaned = strip_jsonc(raw) if raw.strip() else ""
    data = json.loads(cleaned) if cleaned.strip() else {}
    cur = data
    for key in keypath.split(".")[:-1]:
        cur = cur.setdefault(key, {})
    cur[keypath.split(".")[-1]] = block
    mkdir_for(target)
    json.dump(data, open(target, "w"), indent=2)
    print("  patched (jsonc->json, comments stripped):", target)


def patch_yaml(target):
    backup(target)
    content = open(target).read() if (os.path.exists(target) and os.path.getsize(target)) else ""
    if (MARKER in content) and not force:
        print("  already patched (marker):", target); return
    lines = content.split("\n")
    base_block = [
        "  continuum:",
        "    enabled: true",
        f"    command: {wrapper}",
        "    args: []",
        "    env:",
        f"      ELING_HOME: {eling_home}",
        "    timeout: 120",
    ]
    marker_open = "  " + MARKER
    marker_close = "  # <<< eling-continuum <<<"
    idx = next((i for i, l in enumerate(lines) if re.match(r"^mcp_servers\s*:\s*$", l)), None)
    if idx is None:
        out = lines + [MARKER, "mcp_servers:"] + base_block + ["# <<< eling-continuum <<<"]
    else:
        base_indent = len(lines[idx]) - len(lines[idx].lstrip())
        j = idx + 1
        while j < len(lines):
            if lines[j].strip() == "":
                j += 1
                continue
            ind = len(lines[j]) - len(lines[j].lstrip())
            if ind <= base_indent:
                break
            j += 1
        child_text = "\n".join(lines[idx + 1 : j])
        if "continuum:" in child_text:
            print("  WARN continuum already present under mcp_servers in", target); return
        insert = [marker_open] + base_block + [marker_close]
        out = lines[:j] + insert + lines[j:]
    mkdir_for(target)
    open(target, "w").write("\n".join(out))
    print("  patched (yaml, merged under mcp_servers):", target)

if fmt == "json":
    patch_json(target, keypath, block)
elif fmt == "jsonc":
    patch_jsonc(target, keypath, block)
elif fmt == "toml":
    patch_toml(target)
elif fmt == "yaml":
    patch_yaml(target)
else:
    print("  unknown format:", fmt); sys.exit(1)
PYEOF
}

echo "Eling Continuum installer"
echo "  wrapper : $WRAPPER"
echo "  ELING_HOME: $ELING_HOME"
echo "  dry-run : $DRY_RUN"
echo "  agents  : ${AGENTS:-all}"
echo "---------------------------------------------------"

if want hermes; then
  echo "[hermes] ~/.hermes/config.yaml"
  patch yaml "$HOME/.hermes/config.yaml" "" ""
fi

if want opencode; then
  echo "[opencode] ~/.config/opencode/opencode.jsonc  (MiMo-Code uses the same file)"
  patch jsonc "$HOME/.config/opencode/opencode.jsonc" "mcp.continuum" \
    "{\"type\":\"local\",\"command\":[\"$WRAPPER\"],\"env\":{\"ELING_HOME\":\"$ELING_HOME\"}}"
  echo "  (MiMo-Code: reuses OpenCode config — covered by the above)"
fi

if want zero; then
  echo "[zero] ~/.config/zero/config.json"
  patch json "$HOME/.config/zero/config.json" "mcp.servers.continuum" \
    "{\"type\":\"stdio\",\"command\":\"$WRAPPER\",\"args\":[],\"env\":{\"ELING_HOME\":\"$ELING_HOME\"}}"
fi

if want claude-code; then
  echo "[claude-code] ~/.claude.json"
  patch json "$HOME/.claude.json" "mcpServers.continuum" \
    "{\"command\":\"$WRAPPER\",\"env\":{\"ELING_HOME\":\"$ELING_HOME\"}}"
fi

if want codex; then
  echo "[codex] ~/.codex/config.toml"
  patch toml "$HOME/.codex/config.toml" "" ""
fi

echo "---------------------------------------------------"
echo "Done. Restart/reload each agent (e.g. Hermes /reload-mcp)."
echo "Verify: printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"clientInfo\":{\"name\":\"probe\"}}}' | $WRAPPER"

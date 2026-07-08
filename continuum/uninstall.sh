#!/bin/sh
# uninstall.sh — remove the Eling Continuum MCP entry from every agent config.
#
# Two strategies, in order:
#   1. If a <file>.bak-continuum backup exists (created by install.sh), restore it.
#   2. Otherwise, strip the continuum block (marker-delimited for yaml/toml,
#      or the "continuum" key for json/jsonc) in place.
#
# Usage:
#   continuum/uninstall.sh                 # all agents
#   continuum/uninstall.sh --agents hermes,zero
#   continuum/uninstall.sh --dry-run
#   continuum/uninstall.sh --keep-backups  # restore but don't delete .bak-continuum
#
# Supported agents: hermes, opencode (+mimo), zero, claude-code, codex

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

AGENTS=""
DRY_RUN=0
KEEP_BACKUPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --agents)      AGENTS="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --keep-backups) KEEP_BACKUPS=1; shift ;;
    -h|--help)     sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

want() {
  [ -z "$AGENTS" ] && return 0
  echo ",$AGENTS," | grep -q ",$1,"
}

# ---- embedded Python restorer (stdlib only) ----
restore() {
  # $1 target file
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] would restore: $1"
    return 0
  fi
  python3 - "$1" "$KEEP_BACKUPS" <<'PYEOF'
import sys, os, re, shutil

def main():
    target, keep = sys.argv[1], sys.argv[2] == "1"
    bak = target + ".bak-continuum"
    MARKER = "# >>> eling-continuum >>>"

    if os.path.exists(bak) and os.path.getsize(bak):
        shutil.move(bak, target)
        print("  restored from backup:", target)
        return

    if not (os.path.exists(target) and os.path.getsize(target)):
        print("  nothing to do (file absent):", target); return

    raw = open(target).read()
    lower = target.lower()

    def strip_marker_block(text):
        pat = re.compile(re.escape(MARKER) + r".*?# <<< eling-continuum <<<", re.S)
        return pat.sub("", text).rstrip() + "\n"

    if lower.endswith(".toml"):
        text = strip_marker_block(raw)
        text = re.sub(r'\[mcp_servers\.continuum\][^\[]*', '', text, flags=re.S)
        open(target, "w").write(text)
        print("  stripped continuum block:", target)
    elif lower.endswith((".yaml", ".yml")):
        text = strip_marker_block(raw)
        lines = text.split("\n")
        out, skip = [], False
        for ln in lines:
            if re.match(r"^\s*continuum:\s*$", ln):
                skip = True
                continue
            if skip:
                if ln.strip() == "":
                    continue
                if len(ln) - len(ln.lstrip()) > 0 and not ln.lstrip().startswith("#"):
                    continue
                skip = False
            out.append(ln)
        open(target, "w").write("\n".join(out).rstrip() + "\n")
        print("  stripped continuum block:", target)
    else:
        m = re.search(r'"continuum"\s*:\s*', raw)
        if not m:
            print("  continuum key not found:", target); return
        start = m.start()
        i = raw.find("{", start)
        if i != -1:
            depth, j = 0, i
            while j < len(raw):
                if raw[j] == "{": depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
                j += 1
            val = raw[start:end]
        else:
            eq = raw.find(":", start) + 1
            rest = raw[eq:].lstrip()
            if rest.startswith('"'):
                q = rest.find('"', 1)
                end = eq + (len(raw[eq:]) - len(rest)) + q + 1
            else:
                mm = re.match(r'\w+', rest)
                end = eq + (len(raw[eq:]) - len(rest)) + (mm.end() if mm else 0)
            val = raw[start:end]
        pre = raw[:start].rstrip()
        post = raw[end:].lstrip()
        if pre.endswith(","):
            new = raw[: len(pre) - 1].rstrip() + post
        elif post.startswith(","):
            new = pre + post[1:]
        else:
            new = pre + post
        open(target, "w").write(new)
        print("  stripped continuum key:", target)

main()
PYEOF
}

echo "Eling Continuum uninstaller"
echo "  dry-run : $DRY_RUN"
echo "  agents  : ${AGENTS:-all}"
echo "---------------------------------------------------"

if want hermes; then
  echo "[hermes] ~/.hermes/config.yaml"; restore "$HOME/.hermes/config.yaml"
fi
if want opencode; then
  echo "[opencode] ~/.config/opencode/opencode.jsonc (MiMo-Code same file)"
  restore "$HOME/.config/opencode/opencode.jsonc"
fi
if want zero; then
  echo "[zero] ~/.config/zero/config.json"; restore "$HOME/.config/zero/config.json"
fi
if want claude-code; then
  echo "[claude-code] ~/.claude.json"; restore "$HOME/.claude.json"
fi
if want codex; then
  echo "[codex] ~/.codex/config.toml"; restore "$HOME/.codex/config.toml"
fi

echo "---------------------------------------------------"
echo "Done. Restart/reload each agent to drop the continuum tools."

#!/bin/sh
# healthcheck.sh — verify all Eling MCP servers are alive and agents are wired.
#
# Checks:
#   0. LOCAL MCP SERVERS: check as-brain-mcp and eling-termux-mcp (local launchers)
#      respond to MCP initialize + tools/list.
#   1. CONTINUUM LIVE: spawn continuum.sh, run initialize + tools/list, confirm
#      all expected continuum_* tools are exposed.
#   2. WIRED: for each agent, confirm its config file references the wrapper
#      and the wrapper is executable.
#   3. CACHES: show size/age of persistent caches, flag stale ones.
#   4. DB: show orchestration state (agents by status, knowledge count, projects).
#
# Usage:
#   continuum/healthcheck.sh                        # auto-detect paths
#   continuum/healthcheck.sh --eling-home /data/store
#   continuum/healthcheck.sh --eling-path /opt/eling
#   continuum/healthcheck.sh --agents hermes,zero

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEFAULT_WRAPPER="$REPO_ROOT/src/eling/continuum/continuum.sh"

ELING_PATH="$DEFAULT_WRAPPER"
ELING_HOME="${ELING_HOME:-$HOME/.eling}"
AGENTS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --eling-path)  ELING_PATH="$2"; shift 2 ;;
    --eling-home)  ELING_HOME="$2"; shift 2 ;;
    --agents)      AGENTS="$2"; shift 2 ;;
    -h|--help)     sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

case "$ELING_PATH" in
  /*) WRAPPER="$ELING_PATH" ;;
  *)  WRAPPER="$(cd -- "$ELING_PATH" 2>/dev/null && pwd)/continuum.sh" ;;
esac
case "$WRAPPER" in
  */continuum.sh) : ;;
  *) WRAPPER="$WRAPPER/src/eling/continuum/continuum.sh" ;;
esac

if [ ! -f "$WRAPPER" ]; then
  echo "ERROR: continuum.sh not found at: $WRAPPER" >&2; exit 1
fi

want() {
  [ -z "$AGENTS" ] && return 0
  echo ",$AGENTS," | grep -q ",$1,"
}

EXPECTED_CONTINUUM_TOOLS="continuum_project_create continuum_project_get continuum_project_list continuum_knowledge_create continuum_knowledge_get continuum_knowledge_list continuum_knowledge_search continuum_agent_register continuum_agent_update continuum_agent_get continuum_registry_list continuum_plot_get continuum_plot_update continuum_dispatch continuum_reservations"

PASS=0; FAIL=0; SKIP=0
ok()    { echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad()   { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
skip()  { echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

echo "Eling Healthcheck"
echo "  wrapper    : $WRAPPER"
echo "  ELING_HOME : $ELING_HOME"
echo "  agents     : ${AGENTS:-all}"
echo "---------------------------------------------------"

# Locate the `eling` package to resolve launcher scripts
ELING_PKG_SRC="$REPO_ROOT/src/eling"
LAUNCHER_DIR="${HOME}/.local/bin"

# Helper: MCP stdio handshake against a command
# Usage: _mcp_check <label> <command> <expected-server-name> <min-tool-count>
_mcp_check() {
  label="$1"; cmd="$2"; srvname="$3"; mintools="$4"
  if [ ! -x "$cmd" ] && ! command -v "$(echo "$cmd" | cut -d' ' -f1)" >/dev/null 2>&1; then
    skip "$label — launcher not found/executable: $cmd"
    return
  fi
  RESP=$(printf '%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"healthcheck","version":"1.0"}}}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | timeout 10 $cmd 2>/dev/null || true)
  if echo "$RESP" | grep -q "\"name\":[ ]*\"$srvname\""; then
    ok "$label — initialize handshake OK ($srvname)"
  else
    bad "$label — initialize handshake failed (no server banner)"
    return
  fi
  # Use grep to count tool names (case-insensitive, handle any prefix)
  TOOLCOUNT=$(echo "$RESP" | grep -oE '"name":[ ]*"[a-z]+_[a-z_]+"' | sort -u | wc -l)
  if [ "$TOOLCOUNT" -ge "$mintools" ]; then
    ok "$label — tools/list returned $TOOLCOUNT tools"
  else
    bad "$label — tools/list returned $TOOLCOUNT tools (expected >= $mintools)"
  fi
}

# ---- 0. LOCAL MCP SERVERS ----
echo "[0/4] local MCP server health"

# Try the Termux launchers first, fall back to direct python -m
if [ -x "${LAUNCHER_DIR}/as-brain-mcp" ]; then
  AS_BRAIN_CMD="${LAUNCHER_DIR}/as-brain-mcp"
elif [ -x "${LAUNCHER_DIR}/eling-termux-mcp" ]; then
  AS_BRAIN_CMD="${LAUNCHER_DIR}/eling-termux-mcp"
else
  AS_BRAIN_CMD="python3 -m eling.as_brain.mcp_server"
fi

_mcp_check "as-brain-mcp" "$AS_BRAIN_CMD" "as-brain" 15

if [ -x "${LAUNCHER_DIR}/eling-termux-mcp" ]; then
  ELING_MCP_CMD="${LAUNCHER_DIR}/eling-termux-mcp"
else
  ELING_MCP_CMD="python3 -m eling.mcp_server"
fi
_mcp_check "eling-mcp" "$ELING_MCP_CMD" "eling-notion" 5

echo "---------------------------------------------------"

# ---- 1. CONTINUUM LIVE stdio handshake ----
echo "[1/4] continuum stdio handshake"
if [ ! -x "$WRAPPER" ] && [ ! -f "$WRAPPER" ]; then
  bad "wrapper not found: $WRAPPER"
elif [ ! -x "$WRAPPER" ]; then
  bad "wrapper not executable: $WRAPPER (chmod +x it)"
else
  RESP=$(PYTHONUNBUFFERED=1 ELING_HOME="$ELING_HOME" printf '%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"healthcheck","version":"1.0"}}}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | timeout 10 "$WRAPPER" 2>/dev/null || true)
  if echo "$RESP" | grep -q '"name":[ ]*"eling-continuum"'; then
    ok "initialize handshake OK (eling-continuum)"
  else
    bad "initialize handshake failed (no server banner)"
  fi
  TOOLCOUNT=$(echo "$RESP" | grep -oE '"name":[ ]*"continuum_[a-z_]+"' | sort -u | wc -l)
  if [ "$TOOLCOUNT" -ge 15 ]; then
    ok "tools/list returned $TOOLCOUNT continuum_* tools"
  else
    bad "tools/list returned only $TOOLCOUNT continuum_* tools (expected >= 15)"
  fi
  MISSING=""
  for t in $EXPECTED_CONTINUUM_TOOLS; do
    echo "$RESP" | grep -q "\"name\":[ ]*\"$t\"" || MISSING="$MISSING $t"
  done
  if [ -z "$MISSING" ]; then
    ok "all expected continuum tools present"
  else
    bad "missing tools:$MISSING"
  fi
fi

echo "---------------------------------------------------"

# ---- 2. WIRED config check ----
echo "[2/4] agent config wiring"

check_json() {  # $1 file  $2 needle
  f="$1"; needle="$2"
  if [ ! -f "$f" ]; then skip "config not found: $f"; return; fi
  if grep -q "$needle" "$f" && grep -q "$WRAPPER" "$f"; then
    ok "$(basename "$f") -> continuum wired to wrapper"
  elif grep -q "$needle" "$f"; then
    bad "$(basename "$f") references continuum but NOT the wrapper ($WRAPPER)"
  else
    bad "$(basename "$f") has no continuum entry"
  fi
}

check_codex() {  # $1 toml file
  f="$1"
  if [ ! -f "$f" ]; then skip "codex config not found: $f"; return; fi
  if grep -q 'mcp_servers.continuum' "$f" && grep -q "$WRAPPER" "$f"; then
    ok "codex config references continuum + wrapper"
  elif grep -q 'mcp_servers.continuum' "$f"; then
    bad "codex config references continuum but NOT the wrapper"
  else
    bad "codex config has no continuum entry"
  fi
}

if want hermes; then
  echo "[hermes] checking configs"
  f="$HOME/.hermes/config.yaml"
  if [ ! -f "$f" ]; then f="$HOME/.config/hermes/config.yaml"; fi
  check_json "$f" 'continuum'
fi

if want opencode; then
  echo "[opencode] checking configs"
  f="$HOME/.config/opencode/opencode.jsonc"
  check_json "$f" 'continuum'
fi

if want zero; then
  echo "[zero] checking configs"
  f="$HOME/.config/zero/config.json"
  check_json "$f" 'continuum'
fi

if want claude-code; then
  echo "[claude-code] checking configs"
  f="$HOME/.claude.json"
  check_json "$f" 'continuum'
fi

if want codex; then
  echo "[codex] checking configs"
  f="$HOME/.codex/config.toml"
  check_codex "$f"
fi

# Also check if the Termux launchers exist and are executable
echo ""
echo "  Termux launchers:"
for launcher in as-brain-mcp eling-termux-mcp eling-termux; do
  lp="${LAUNCHER_DIR}/${launcher}"
  if [ -x "$lp" ]; then
    ok "$launcher — executable at $lp"
  elif [ -f "$lp" ]; then
    bad "$launcher — exists but not executable at $lp"
  else
    skip "$launcher — not found at $lp"
  fi
done

echo "---------------------------------------------------"

# ---- 3. CACHE STALENESS ----
echo "[3/4] cache health (ELING_HOME=$ELING_HOME)"
CACHE_STALE=0
if [ -d "$ELING_HOME" ]; then
  for f in "$ELING_HOME"/*.json "$ELING_HOME"/.*.json; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    size=$(wc -c < "$f" 2>/dev/null | tr -d ' ')
    mtime=$(stat -c '%Y' "$f" 2>/dev/null || stat -f '%m' "$f" 2>/dev/null || echo "0")
    now=$(date '+%s')
    age_days=$(( (now - mtime) / 86400 ))
    if [ "$size" -gt 10485760 ] && [ "$age_days" -gt 1 ]; then
      bad "$name — ${size}B, ${age_days}d old (stale, >10MB + >1d)"
      CACHE_STALE=$((CACHE_STALE+1))
    elif [ "$size" -gt 10485760 ]; then
      ok "$name — ${size}B (large but fresh)"
    elif [ "$size" -gt 0 ]; then
      ok "$name — ${size}B, ${age_days}d old"
    fi
  done
  # Check DB freshness
  for db in facts.db kb.db continuum.db; do
    dbpath="$ELING_HOME/$db"
    [ -f "$dbpath" ] || continue
    mtime=$(stat -c '%Y' "$dbpath" 2>/dev/null || stat -f '%m' "$dbpath" 2>/dev/null || echo "0")
    now=$(date '+%s')
    age_hrs=$(( (now - mtime) / 3600 ))
    size=$(wc -c < "$dbpath" 2>/dev/null | tr -d ' ')
    if [ "$age_hrs" -gt 48 ]; then
      skip "$db — ${size}B, last write ${age_hrs}h ago (dormant)"
    else
      ok "$db — ${size}B, last write ${age_hrs}h ago"
    fi
  done
else
  skip "ELING_HOME directory not found: $ELING_HOME"
fi

if [ "$CACHE_STALE" -gt 0 ]; then
  echo "  (Tip: delete stale .json caches with: rm -f $ELING_HOME/*.json $ELING_HOME/.*.json)"
fi

echo "---------------------------------------------------"

# ---- 4. DB state ----
echo "[4/4] orchestration state (ELING_HOME=$ELING_HOME)"
DB="$ELING_HOME/continuum.db"
if [ ! -f "$DB" ]; then
  echo "  (no continuum.db yet at $DB — hub unused; run continuum_dispatch to seed it)"
else
  python3 - "$DB" <<'PYEOF'
import sys, sqlite3
db = sys.argv[1]
try:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    def one(q, p=()):
        r = con.execute(q, p).fetchone()
        return r[0] if r else 0
    def rows(q, p=()):
        return con.execute(q, p).fetchall()
    print("  db mode     :", con.execute("PRAGMA journal_mode").fetchone()[0])
    print("  projects    :", one("SELECT COUNT(*) FROM projects"))
    print("  agents total:", one("SELECT COUNT(*) FROM agents"))
    print("  by status   :", dict((r["status"], r["n"]) for r in rows(
        "SELECT status, COUNT(*) n FROM agents GROUP BY status")))
    print("  knowledge   :", one("SELECT COUNT(*) FROM knowledge"))
    try:
        src = dict((r["agent_slug"], r["n"]) for r in rows(
            "SELECT agent_slug, COUNT(*) n FROM knowledge GROUP BY agent_slug"))
        if src:
            print("  knowledge by agent_slug:", src)
    except Exception as e:
        print("  (knowledge attribution unavailable:", e, ")")
    con.close()
except Exception as e:
    print(f"  error reading db: {e}")
PYEOF
fi

echo "---------------------------------------------------"
echo "Result: $PASS OK, $FAIL FAIL, $SKIP SKIP"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

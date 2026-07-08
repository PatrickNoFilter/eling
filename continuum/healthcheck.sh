#!/bin/sh
# healthcheck.sh — verify the Eling Continuum hub is alive and every agent is wired.
#
# Three checks:
#   1. LIVE: spawn continuum.sh, run MCP initialize + tools/list, confirm all
#      expected continuum_* tools are exposed (server reachable over stdio).
#   2. WIRED: for each agent, confirm its config file references the wrapper and
#      the wrapper is executable (so the agent *would* connect on launch).
#   3. DB: show orchestration state (agents by status, knowledge count, projects).
#
# Usage:
#   continuum/healthcheck.sh                # auto-detect path + ELING_HOME=~/.eling
#   continuum/healthcheck.sh --eling-home /data/store
#   continuum/healthcheck.sh --eling-path /opt/eling
#   continuum/healthcheck.sh --agents hermes,zero

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEFAULT_WRAPPER="$REPO_ROOT/src/eling/continuum/continuum.sh"

ELING_PATH="$DEFAULT_WRAPPER"
ELING_HOME="$HOME/.eling"
AGENTS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --eling-path)  ELING_PATH="$2"; shift 2 ;;
    --eling-home)  ELING_HOME="$2"; shift 2 ;;
    --agents)      AGENTS="$2"; shift 2 ;;
    -h|--help)     sed -n '2,18p' "$0"; exit 0 ;;
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

EXPECTED_TOOLS="continuum_project_create continuum_project_get continuum_project_list continuum_knowledge_create continuum_knowledge_get continuum_knowledge_list continuum_knowledge_search continuum_agent_register continuum_agent_update continuum_agent_get continuum_registry_list continuum_plot_get continuum_plot_update continuum_dispatch continuum_reservations"

PASS=0; FAIL=0
ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "Eling Continuum healthcheck"
echo "  wrapper    : $WRAPPER"
echo "  ELING_HOME : $ELING_HOME"
echo "  agents     : ${AGENTS:-all}"
echo "---------------------------------------------------"

# ---- 1. LIVE stdio handshake ----
echo "[1/3] live stdio handshake"
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
    bad "tools/list returned only $TOOLCOUNT continuum_* tools (expected 15)"
  fi
  MISSING=""
  for t in $EXPECTED_TOOLS; do
    echo "$RESP" | grep -q "\"name\":[ ]*\"$t\"" || MISSING="$MISSING $t"
  done
  if [ -z "$MISSING" ]; then
    ok "all expected tools present"
  else
    bad "missing tools:$MISSING"
  fi
fi

echo "---------------------------------------------------"

# ---- 2. WIRED config check ----
echo "[2/3] agent config wiring"

check_json() {  # $1 file  $2 jq-style key regex fragment
  f="$1"; needle="$2"
  if [ ! -f "$f" ]; then bad "config missing: $f"; return; fi
  if grep -q "$needle" "$f" && grep -q "$WRAPPER" "$f"; then
    ok "$(basename "$f") -> continuum wired to wrapper"
  elif grep -q "$needle" "$f"; then
    bad "$(basename "$f") references continuum but NOT the wrapper ($WRAPPER)"
  else
    bad "$(basename "$f") has no continuum entry"
  fi
}

if want hermes; then
  echo "[hermes] ~/.hermes/config.yaml"
  f="$HOME/.hermes/config.yaml"
  if [ ! -f "$f" ]; then bad "config missing: $f";
  elif grep -q 'continuum' "$f" && grep -q "$WRAPPER" "$f"; then ok "config references continuum + wrapper";
  elif grep -q 'continuum' "$f"; then bad "references continuum but not the wrapper";
  else bad "no continuum entry under mcp_servers"; fi
fi

if want opencode; then
  echo "[opencode] ~/.config/opencode/opencode.jsonc (MiMo-Code shares this)"
  check_json "$HOME/.config/opencode/opencode.jsonc" 'continuum'
fi

if want zero; then
  echo "[zero] ~/.config/zero/config.json"
  check_json "$HOME/.config/zero/config.json" 'continuum'
fi

if want claude-code; then
  echo "[claude-code] ~/.claude.json"
  check_json "$HOME/.claude.json" 'continuum'
fi

if want codex; then
  echo "[codex] ~/.codex/config.toml"
  f="$HOME/.codex/config.toml"
  if [ ! -f "$f" ]; then bad "config missing: $f";
  elif grep -q 'mcp_servers.continuum' "$f" && grep -q "$WRAPPER" "$f"; then ok "config references mcp_servers.continuum + wrapper";
  elif grep -q 'mcp_servers.continuum' "$f"; then bad "references continuum but not the wrapper";
  else bad "no mcp_servers.continuum entry"; fi
fi

echo "---------------------------------------------------"

# ---- 3. DB state ----
echo "[3/3] orchestration state (ELING_HOME=$ELING_HOME)"
DB="$ELING_HOME/continuum.db"
if [ ! -f "$DB" ]; then
  echo "  (no continuum.db yet at $DB — hub unused; run continuum_dispatch to seed it)"
else
  python3 - "$DB" "$WRAPPER" <<'PYEOF'
import sys, os, sqlite3, json
db, wrapper = sys.argv[1], sys.argv[2]
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
# Show which client sources have registered anything (attribution).
try:
    src = dict((r["agent_slug"], r["n"]) for r in rows(
        "SELECT agent_slug, COUNT(*) n FROM knowledge GROUP BY agent_slug"))
    if src:
        print("  knowledge by agent_slug:", src)
except Exception as e:
    print("  (knowledge attribution unavailable:", e, ")")
con.close()
PYEOF
fi

echo "---------------------------------------------------"
echo "Result: $PASS OK, $FAIL FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

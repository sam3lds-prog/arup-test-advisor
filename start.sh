#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh  v2.1.0  —  ARUP AI Test Advisor launcher
# ─────────────────────────────────────────────────────────────────────────────
#
# Fixes in v2.1.0:
#   • After killing old process, polls until port is actually FREE (not just
#     sleep 1) — macOS keeps TCP sockets in TIME_WAIT for up to 60 s after kill
#   • If port stays occupied after 15 s, automatically increments to next
#     available port (8001, 8002 …) instead of blocking forever
#   • Health check uses python3 urllib (always available) instead of curl
#   • Shows last 20 lines of backend log immediately if process crashes
#   • Progress dots while waiting so the terminal doesn't look frozen
#   • Default kill answer changed to Y (most common intent)
#
# Usage:
#   ./start.sh              # dev mode  (hot-reload backend, Vite dev server)
#   ./start.sh --prod       # prod mode (build frontend, serve static)
#   ./start.sh --api-only   # backend only (no frontend process)
#   ./start.sh --logs       # tail combined log after launch
#
# ─────────────────────────────────────────────────────────────────────────────

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${GREEN}▶${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $1"; }
error()   { echo -e "${RED}✖${NC}  $1"; exit 1; }
step()    { echo -e "\n${BLUE}${BOLD}$1${NC}"; }
divider() { echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
MODE="dev"
API_ONLY=false
SHOW_LOGS=false

for arg in "$@"; do
  case "$arg" in
    --prod)     MODE="prod" ;;
    --api-only) API_ONLY=true ;;
    --logs)     SHOW_LOGS=true ;;
    --help|-h)
      echo ""
      echo "  ARUP AI Test Advisor — start.sh v2.1.0"
      echo ""
      echo "  Usage:"
      echo "    ./start.sh              dev mode  (default)"
      echo "    ./start.sh --prod       production build + serve"
      echo "    ./start.sh --api-only   backend only"
      echo "    ./start.sh --logs       tail logs after launch"
      echo ""
      exit 0
      ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOGS="$ROOT/logs"
BACKEND_LOG="$LOGS/backend.log"
FRONTEND_LOG="$LOGS/frontend.log"
PID_FILE="$ROOT/.pids"

mkdir -p "$LOGS"

# ── Starting port preferences (may be bumped automatically) ───────────────────
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-5174}"

# ── Port helpers ──────────────────────────────────────────────────────────────

# Returns 0 (true) if the port is occupied, 1 (false) if free
_port_in_use() {
  local port="$1"
  if command -v lsof &>/dev/null; then
    lsof -iTCP:"$port" -sTCP:LISTEN -t &>/dev/null
  else
    nc -z 127.0.0.1 "$port" &>/dev/null
  fi
}

# Kill all PIDs listening on a port
_kill_port() {
  local port="$1"
  local pids
  pids=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | tr '\n' ' ')
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    info "Killed PID(s) $pids (was on :$port)"
  fi
}

# Wait until a port is free, printing progress dots.
# Returns 0 if port became free within timeout, 1 if it timed out.
_wait_port_free() {
  local port="$1"
  local max="${2:-15}"
  local elapsed=0
  printf "  Waiting for :$port to be released"
  while _port_in_use "$port"; do
    printf "."
    sleep 1
    elapsed=$((elapsed + 1))
    if [ "$elapsed" -ge "$max" ]; then
      echo " (timed out after ${max}s)"
      return 1
    fi
  done
  echo " free ✓"
  return 0
}

# Find the next free port starting from $1, store result in variable named $2
_find_free_port() {
  local start="$1"
  local varname="$2"
  local port="$start"
  while _port_in_use "$port"; do
    port=$((port + 1))
    if [ "$port" -gt $((start + 20)) ]; then
      error "No free port found in range $start–$((start + 20)). Free up a port and retry."
    fi
  done
  eval "$varname=$port"
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
step "Pre-flight checks"

command -v python3 &>/dev/null || error "Python 3 not found. Install from https://python.org"
command -v node    &>/dev/null || error "Node.js not found. Install from https://nodejs.org"
command -v npm     &>/dev/null || error "npm not found (comes with Node.js)"

PY_VER=$(python3 -c 'import sys; print(sys.version_info >= (3,9))')
[ "$PY_VER" = "True" ] || error "Python 3.9+ required. Current: $(python3 --version)"

info "Python $(python3 --version | cut -d' ' -f2)  ·  Node $(node --version)"

# ── Backend port resolution ───────────────────────────────────────────────────
step "Port allocation"

if _port_in_use "$BACKEND_PORT"; then
  warn "Port $BACKEND_PORT is already in use."
  read -rp "  Kill the existing process on :$BACKEND_PORT? [Y/n] " yn
  yn="${yn:-Y}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    _kill_port "$BACKEND_PORT"
    # Wait for macOS TIME_WAIT to clear — up to 15 s
    if ! _wait_port_free "$BACKEND_PORT" 15; then
      warn "Port $BACKEND_PORT still occupied — trying next available port…"
      _find_free_port "$((BACKEND_PORT + 1))" BACKEND_PORT
      info "Will use port $BACKEND_PORT instead"
    fi
  else
    warn "Finding an alternative backend port automatically…"
    _find_free_port "$((BACKEND_PORT + 1))" BACKEND_PORT
    info "Will use port $BACKEND_PORT"
  fi
fi

info "Backend port: $BACKEND_PORT ✓"

# Frontend port resolution (auto-find, no kill needed)
if [ "$API_ONLY" = false ]; then
  if _port_in_use "$FRONTEND_PORT"; then
    warn "Port $FRONTEND_PORT in use — finding next available frontend port…"
    _find_free_port "$((FRONTEND_PORT + 1))" FRONTEND_PORT
    info "Frontend will use port $FRONTEND_PORT"
  fi
  info "Frontend port: $FRONTEND_PORT ✓"
fi

# ── API key ───────────────────────────────────────────────────────────────────
step "API key"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  if [ -f "$BACKEND/.env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "$BACKEND/.env" | xargs 2>/dev/null) || true
  fi
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] || [ "$ANTHROPIC_API_KEY" = "your_api_key_here" ]; then
  warn "ANTHROPIC_API_KEY is not set."
  echo ""
  read -rsp "  Paste your Anthropic API key (input hidden): " KEY
  echo ""
  if [ -z "$KEY" ]; then
    error "API key cannot be empty."
  fi
  mkdir -p "$BACKEND"
  echo "ANTHROPIC_API_KEY=$KEY" > "$BACKEND/.env"
  export ANTHROPIC_API_KEY="$KEY"
  info "Key saved to backend/.env"
else
  info "API key loaded ✓"
fi

# ── Python virtual environment ────────────────────────────────────────────────
step "Python environment"

VENV="$BACKEND/.venv"
if [ ! -d "$VENV" ]; then
  info "Creating virtual environment…"
  python3 -m venv "$VENV"
fi

info "Installing/verifying Python dependencies…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$BACKEND/requirements.txt"
info "Python dependencies ready ✓"

# ── Node dependencies ─────────────────────────────────────────────────────────
if [ "$API_ONLY" = false ]; then
  step "Node dependencies"
  if [ ! -d "$FRONTEND/node_modules" ]; then
    info "Installing Node dependencies…"
    npm --prefix "$FRONTEND" install --silent
    info "Node dependencies installed ✓"
  else
    info "Node dependencies already present ✓"
  fi
fi

# ── Data directories ──────────────────────────────────────────────────────────
mkdir -p "$ROOT/data/chroma"
mkdir -p "$ROOT/data/knowledge"

# ── Copy formatting_rules.json if not present ─────────────────────────────────
RULES_TARGET="$BACKEND/agents/formatting_rules.json"
if [ ! -f "$RULES_TARGET" ]; then
  if [ -f "$ROOT/formatting_rules.json" ]; then
    cp "$ROOT/formatting_rules.json" "$RULES_TARGET"
    info "formatting_rules.json installed → backend/agents/ ✓"
  fi
fi

# ── Production build ──────────────────────────────────────────────────────────
if [ "$MODE" = "prod" ] && [ "$API_ONLY" = false ]; then
  step "Production build"
  info "Building frontend…"
  npm --prefix "$FRONTEND" run build 2>&1 | tail -5
  info "Frontend built → $FRONTEND/dist ✓"
fi

# ── Launch backend ─────────────────────────────────────────────────────────────
step "Starting services"

if [ "$MODE" = "dev" ]; then
  UVICORN_ARGS="main:app --host 0.0.0.0 --port $BACKEND_PORT --reload"
else
  UVICORN_ARGS="main:app --host 0.0.0.0 --port $BACKEND_PORT --workers 2"
fi

# Fresh log each launch
echo "=== Backend started at $(date) ===" > "$BACKEND_LOG"
echo "=== Frontend started at $(date) ===" > "$FRONTEND_LOG"

info "Starting backend on http://localhost:$BACKEND_PORT …"
cd "$BACKEND"
"$VENV/bin/uvicorn" $UVICORN_ARGS >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
cd "$ROOT"

# ── Health check — python3 urllib (no curl dependency) ───────────────────────
info "Waiting for backend to become ready…"
HEALTH_URL="http://localhost:$BACKEND_PORT/health"
MAX_WAIT=60
ELAPSED=0
HEALTHY=false

while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do

  # Has the process already crashed? (import error, port conflict, etc.)
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo ""
    warn "Backend process exited immediately."
    echo ""
    echo "  Last ${BOLD}20 lines${NC} of backend log:"
    echo "  ────────────────────────────────────────────────"
    tail -20 "$BACKEND_LOG" 2>/dev/null | sed 's/^/  /' || echo "  (no log output)"
    echo "  ────────────────────────────────────────────────"
    echo ""
    error "Fix the error above, then re-run ./start.sh"
  fi

  # HTTP probe via python3 (always available on macOS)
  if python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('$HEALTH_URL', timeout=2)
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    HEALTHY=true
    break
  fi

  printf "."
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done
echo ""   # newline after progress dots

if [ "$HEALTHY" = true ]; then
  info "Backend is healthy ✓"
else
  warn "Backend did not respond within ${MAX_WAIT}s — it may still be loading."
  warn "Check logs: $BACKEND_LOG"
fi

# ── Write backend port for Vite proxy ────────────────────────────────────────
# vite_config.js reads VITE_BACKEND_PORT from .env.local so the proxy always
# targets the port we actually bound to (may differ from 8000 if it was busy).
FRONTEND_ENV="$FRONTEND/.env.local"
printf "VITE_BACKEND_PORT=%s\n" "$BACKEND_PORT" > "$FRONTEND_ENV"
info "Proxy target set → http://localhost:$BACKEND_PORT (written to frontend/.env.local)"

# ── Launch frontend ────────────────────────────────────────────────────────────
FRONTEND_URL=""
FRONTEND_PID=""

if [ "$API_ONLY" = false ]; then
  if [ "$MODE" = "prod" ]; then
    info "Serving production build on http://localhost:$FRONTEND_PORT …"
    cd "$FRONTEND"
    npx vite preview --port "$FRONTEND_PORT" >> "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    cd "$ROOT"
  else
    info "Starting Vite dev server on http://localhost:$FRONTEND_PORT …"
    cd "$FRONTEND"
    # Pass --port explicitly so Vite uses our (possibly bumped) port
    npm run dev -- --port "$FRONTEND_PORT" >> "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    cd "$ROOT"
  fi
  FRONTEND_URL="http://localhost:$FRONTEND_PORT"

  # Wait for frontend to bind (port poll, no sleep guessing)
  FE_ELAPSED=0
  printf "  Waiting for frontend"
  while ! _port_in_use "$FRONTEND_PORT"; do
    printf "."
    sleep 1
    FE_ELAPSED=$((FE_ELAPSED + 1))
    if [ "$FE_ELAPSED" -ge 20 ]; then
      echo " (timeout)"
      warn "Frontend may not have started. Check $FRONTEND_LOG"
      break
    fi
  done
  echo " ready ✓"
fi

# ── Write PID file ─────────────────────────────────────────────────────────────
{
  echo "BACKEND_PID=$BACKEND_PID"
  echo "FRONTEND_PID=${FRONTEND_PID:-}"
  echo "BACKEND_PORT=$BACKEND_PORT"
  echo "FRONTEND_PORT=$FRONTEND_PORT"
  echo "STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$PID_FILE"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
divider
echo -e "${GREEN}  ARUP AI Test Advisor is running  (${BOLD}${MODE} mode${NC}${GREEN})${NC}"
echo ""
if [ -n "$FRONTEND_URL" ]; then
  echo -e "  ${BOLD}→ App:${NC}      $FRONTEND_URL"
fi
echo -e "  ${BOLD}→ API:${NC}      http://localhost:$BACKEND_PORT"
echo -e "  ${BOLD}→ Docs:${NC}     http://localhost:$BACKEND_PORT/docs"
echo -e "  ${BOLD}→ Logs:${NC}     $LOGS/"
echo ""
echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all services"
divider
echo ""

# ── Tail logs (optional) ──────────────────────────────────────────────────────
if [ "$SHOW_LOGS" = true ]; then
  info "Tailing combined logs (Ctrl+C to stop)…"
  tail -f "$BACKEND_LOG" "$FRONTEND_LOG" 2>/dev/null &
  TAIL_PID=$!
fi

# ── Graceful shutdown ─────────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "Shutting down…"
  [ -n "${TAIL_PID:-}"     ] && kill "$TAIL_PID"     2>/dev/null || true
  [ -n "${BACKEND_PID:-}"  ] && kill "$BACKEND_PID"  2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  rm -f "$PID_FILE"
  info "All services stopped."
  exit 0
}
trap cleanup INT TERM

wait
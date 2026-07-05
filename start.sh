#!/usr/bin/env bash
# QuantDeck — start BOTH the backend API and the frontend dev server with one
# command. First run auto-creates the Python venv, installs backend deps, and
# runs `npm install`. Ctrl+C stops both cleanly.
#
#   ./start.sh
#
# Env overrides: QUANTDECK_PORT (backend, default 8000), FRONTEND_PORT (5173).
# Portable across macOS bash 3.2 and newer bash.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT="${QUANTDECK_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_PID=""
FRONTEND_PID=""

# Kill a process and any children (portable: no process-group assumptions).
kill_tree() {
  pid="$1"
  [ -n "$pid" ] || return 0
  # children first
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

CLEANED=""
cleanup() {
  [ -n "$CLEANED" ] && return 0
  CLEANED=1
  echo ""
  echo "Shutting down QuantDeck…"
  kill_tree "$FRONTEND_PID"
  kill_tree "$BACKEND_PID"
  # Belt-and-suspenders: free the ports even if a child re-parented itself.
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
      kill -TERM "$pid" 2>/dev/null || true
    done
  done
  wait 2>/dev/null || true
  echo "Stopped."
}
trap cleanup EXIT INT TERM

# --- Pre-flight: refuse to start if a port is already taken -----------------
for port_pair in "backend:$BACKEND_PORT" "frontend:$FRONTEND_PORT"; do
  name="${port_pair%%:*}"
  port="${port_pair##*:}"
  if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[error] Port $port ($name) is already in use — QuantDeck may already be"
    echo "        running. Free it first, e.g.:  lsof -tiTCP:$port | xargs kill"
    exit 1
  fi
done

# --- First-run setup: backend venv + deps -----------------------------------
if [ ! -d "$BACKEND/.venv" ]; then
  echo "[setup] Creating Python virtualenv + installing backend dependencies…"
  python3 -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install --upgrade pip >/dev/null
  "$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt"
fi
[ -f "$BACKEND/.env" ] || cp "$BACKEND/.env.example" "$BACKEND/.env"

# --- First-run setup: frontend deps -----------------------------------------
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "[setup] Installing frontend dependencies (npm install)…"
  ( cd "$FRONTEND" && npm install )
fi

# --- Start backend ----------------------------------------------------------
echo "[start] Backend API  → http://localhost:$BACKEND_PORT"
(
  cd "$BACKEND"
  exec ./.venv/bin/python -m uvicorn quantdeck.api.app:create_app --factory \
    --host "${QUANTDECK_HOST:-0.0.0.0}" --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

# --- Wait for the backend to be healthy before starting the frontend --------
printf "[start] Waiting for backend to be ready"
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    echo " ✓"
    break
  fi
  # If the backend died during startup, stop here with its output visible.
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo "[error] Backend failed to start. See the output above."
    exit 1
  fi
  printf "."
  sleep 0.5
done

# --- Start frontend dev server (exec vite directly for a clean PID) ---------
echo "[start] Frontend UI  → http://localhost:$FRONTEND_PORT"
(
  cd "$FRONTEND"
  exec npx vite --port "$FRONTEND_PORT" --host
) &
FRONTEND_PID=$!

echo ""
echo "QuantDeck is running:"
echo "  • App (open this):  http://localhost:$FRONTEND_PORT"
echo "  • API docs:         http://localhost:$BACKEND_PORT/docs"
echo ""
echo "Press Ctrl+C to stop both."

# Wait until either process exits; the EXIT trap then stops the other.
# (Portable busy-wait — works on bash 3.2 which lacks `wait -n`.)
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

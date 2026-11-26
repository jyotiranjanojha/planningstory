#!/usr/bin/env bash
set -Eeuo pipefail

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8001}"
NODE_PORT="${PORT:-3000}"
READY_TIMEOUT_SECS="${BACKEND_READY_TIMEOUT_SECS:-45}"

cleanup() {
  local exit_code=$?
  echo "[startup] shutting down services (exit=${exit_code})"

  if [ -n "${NODE_PID:-}" ] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill -TERM "$NODE_PID" >/dev/null 2>&1 || true
    wait "$NODE_PID" >/dev/null 2>&1 || true
  fi

  if [ -n "${PYTHON_PID:-}" ] && kill -0 "$PYTHON_PID" 2>/dev/null; then
    kill -TERM "$PYTHON_PID" >/dev/null 2>&1 || true
    wait "$PYTHON_PID" >/dev/null 2>&1 || true
  fi

  exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "[startup] launching Python FastAPI backend on ${BACKEND_HOST}:${BACKEND_PORT}"
python3 webapp/run.py --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
PYTHON_PID=$!

# Wait for Python + DuckDB V2 readiness before exposing the Node Gateway
elapsed=0
until curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/v2/health" >/dev/null 2>&1; do
  if ! kill -0 "$PYTHON_PID" 2>/dev/null; then
    echo "[startup] Python backend exited during startup"
    exit 1
  fi
  if [ "$elapsed" -ge "$READY_TIMEOUT_SECS" ]; then
    echo "[startup] Python backend did not become ready within ${READY_TIMEOUT_SECS}s"
    exit 1
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done
echo "[startup] Python backend ready in ${elapsed}s"

echo "[startup] launching Node Gateway on 0.0.0.0:${NODE_PORT}"
PORT="$NODE_PORT" PYTHON_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" node src/server.js &
NODE_PID=$!

# Supervise both processes — exit non-zero if either crashes so Rancher/K8s restarts the pod
while kill -0 "$PYTHON_PID" 2>/dev/null && kill -0 "$NODE_PID" 2>/dev/null; do
  sleep 2
done

if ! kill -0 "$PYTHON_PID" 2>/dev/null; then
  echo "[startup] Python backend exited unexpectedly"
  exit 1
fi

if ! kill -0 "$NODE_PID" 2>/dev/null; then
  echo "[startup] Node Gateway exited unexpectedly"
  exit 1
fi

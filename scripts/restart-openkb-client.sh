#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="${OPENKB_CLIENT_HOST:-0.0.0.0}"
PORT="${OPENKB_CLIENT_PORT:-8765}"
STATE_DIR="${OPENKB_CLIENT_STATE_DIR:-$REPO_ROOT/.openkb-client}"
PYTHON_BIN="${OPENKB_CLIENT_PYTHON:-$REPO_ROOT/.venv/bin/python}"
PID_FILE="$STATE_DIR/client-$PORT.pid"
OUT_LOG="$STATE_DIR/client-$PORT.out.log"
ERR_LOG="$STATE_DIR/client-$PORT.err.log"

mkdir -p "$STATE_DIR"

"$SCRIPT_DIR/stop-openkb-client.sh"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found or not executable: $PYTHON_BIN" >&2
  echo "Set OPENKB_CLIENT_PYTHON to the Python executable for this repo." >&2
  exit 1
fi

echo "Starting OpenKB client on http://$HOST:$PORT"
cd "$REPO_ROOT"
if command -v setsid >/dev/null 2>&1; then
  setsid -f "$PYTHON_BIN" -m openkb client --host "$HOST" --port "$PORT" --no-browser \
    >"$OUT_LOG" 2>"$ERR_LOG"
  pid=""
  for _ in $(seq 1 40); do
    pid="$(pgrep -f "openkb client --host $HOST --port $PORT" | head -n 1 || true)"
    [[ -n "$pid" ]] && break
    sleep 0.1
  done
  if [[ -z "$pid" ]]; then
    echo "OpenKB client process was not found after startup. stderr:" >&2
    tail -40 "$ERR_LOG" >&2 || true
    exit 1
  fi
  echo "$pid" > "$PID_FILE"
else
  nohup "$PYTHON_BIN" -m openkb client --host "$HOST" --port "$PORT" --no-browser \
    >"$OUT_LOG" 2>"$ERR_LOG" &
  echo $! > "$PID_FILE"
fi

pid="$(tr -d '[:space:]' < "$PID_FILE")"
deadline=$((SECONDS + 20))
while (( SECONDS < deadline )); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "OpenKB client exited during startup. stderr:" >&2
    tail -40 "$ERR_LOG" >&2 || true
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "http://$HOST:$PORT/" 2>/dev/null || true)" == "200" ]]; then
      echo "OpenKB client started: http://$HOST:$PORT (PID $pid)"
      echo "Logs: $OUT_LOG $ERR_LOG"
      exit 0
    fi
  elif command -v python3 >/dev/null 2>&1; then
    if python3 - "$HOST" "$PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
with socket.create_connection((host, port), timeout=0.5):
    pass
PY
    then
      echo "OpenKB client started: http://$HOST:$PORT (PID $pid)"
      echo "Logs: $OUT_LOG $ERR_LOG"
      exit 0
    fi
  fi
  sleep 0.5
done

echo "OpenKB client did not become ready on http://$HOST:$PORT" >&2
echo "stderr:" >&2
tail -40 "$ERR_LOG" >&2 || true
exit 1

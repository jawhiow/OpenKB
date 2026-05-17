#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="${OPENKB_CLIENT_HOST:-0.0.0.0}"
PORT="${OPENKB_CLIENT_PORT:-8765}"
UI_HOST="${OPENKB_UI_HOST:-127.0.0.1}"
UI_PORT="${OPENKB_UI_PORT:-8764}"
UI_URL="http://$UI_HOST:$UI_PORT"
STATE_DIR="${OPENKB_CLIENT_STATE_DIR:-$REPO_ROOT/.openkb-client}"
PYTHON_BIN="${OPENKB_CLIENT_PYTHON:-$REPO_ROOT/.venv/bin/python}"
NODE_BIN="${OPENKB_UI_NODE_BIN:-$(command -v npm || true)}"
UI_DIR="${OPENKB_UI_DIR:-$REPO_ROOT/openkb-new-ui}"
API_PID_FILE="$STATE_DIR/client-api-$PORT.pid"
API_OUT_LOG="$STATE_DIR/client-api-$PORT.out.log"
API_ERR_LOG="$STATE_DIR/client-api-$PORT.err.log"
UI_PID_FILE="$STATE_DIR/client-ui-$UI_PORT.pid"
UI_OUT_LOG="$STATE_DIR/client-ui-$UI_PORT.out.log"
UI_ERR_LOG="$STATE_DIR/client-ui-$UI_PORT.err.log"

mkdir -p "$STATE_DIR"

"$SCRIPT_DIR/stop-openkb-client.sh"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found or not executable: $PYTHON_BIN" >&2
  echo "Set OPENKB_CLIENT_PYTHON to the Python executable for this repo." >&2
  exit 1
fi

if [[ ! -d "$UI_DIR" ]]; then
  echo "OpenKB new UI directory not found: $UI_DIR" >&2
  exit 1
fi

if [[ -z "$NODE_BIN" ]]; then
  echo "npm not found in PATH. Set OPENKB_UI_NODE_BIN to a working npm executable." >&2
  exit 1
fi

echo "Starting OpenKB client API on http://$HOST:$PORT"
cd "$REPO_ROOT"
if command -v setsid >/dev/null 2>&1; then
  setsid -f "$PYTHON_BIN" -m openkb client --host "$HOST" --port "$PORT" --no-browser \
    >"$API_OUT_LOG" 2>"$API_ERR_LOG"
  pid=""
  for _ in $(seq 1 40); do
    pid="$(pgrep -f "openkb client --host $HOST --port $PORT" | head -n 1 || true)"
    [[ -n "$pid" ]] && break
    sleep 0.1
  done
  if [[ -z "$pid" ]]; then
    echo "OpenKB client process was not found after startup. stderr:" >&2
    tail -40 "$API_ERR_LOG" >&2 || true
    exit 1
  fi
  echo "$pid" > "$API_PID_FILE"
else
  nohup "$PYTHON_BIN" -m openkb client --host "$HOST" --port "$PORT" --no-browser \
    >"$API_OUT_LOG" 2>"$API_ERR_LOG" &
  echo $! > "$API_PID_FILE"
fi

pid="$(tr -d '[:space:]' < "$API_PID_FILE")"
deadline=$((SECONDS + 20))
while (( SECONDS < deadline )); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "OpenKB client exited during startup. stderr:" >&2
    tail -40 "$API_ERR_LOG" >&2 || true
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "http://$HOST:$PORT/" 2>/dev/null || true)" == "200" ]]; then
      break
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
      break
    fi
  fi
  sleep 0.5
done

if (( SECONDS >= deadline )); then
  echo "OpenKB client API did not become ready on http://$HOST:$PORT" >&2
  echo "stderr:" >&2
  tail -40 "$API_ERR_LOG" >&2 || true
  exit 1
fi

echo "Starting OpenKB new UI on $UI_URL"
cd "$UI_DIR"
if command -v setsid >/dev/null 2>&1; then
  setsid -f env PORT="$UI_PORT" HOST="$UI_HOST" OPENKB_API_TARGET="http://127.0.0.1:$PORT" "$NODE_BIN" run dev \
    >"$UI_OUT_LOG" 2>"$UI_ERR_LOG"
  ui_pid=""
  for _ in $(seq 1 60); do
    ui_candidates="$(pgrep -a -f "next dev" 2>/dev/null || true)"
    ui_pid="$(while read -r candidate cmdline; do
      [[ -z "${candidate:-}" ]] && continue
      if [[ "$cmdline" == *"$UI_DIR"* ]]; then
        echo "$candidate"
        break
      fi
    done <<< "$ui_candidates")"
    [[ -n "$ui_pid" ]] && break
    sleep 0.1
  done
  if [[ -z "$ui_pid" ]]; then
    echo "OpenKB new UI process was not found after startup. stderr:" >&2
    tail -60 "$UI_ERR_LOG" >&2 || true
    exit 1
  fi
  echo "$ui_pid" > "$UI_PID_FILE"
else
  nohup env PORT="$UI_PORT" HOST="$UI_HOST" OPENKB_API_TARGET="http://127.0.0.1:$PORT" "$NODE_BIN" run dev \
    >"$UI_OUT_LOG" 2>"$UI_ERR_LOG" &
  echo $! > "$UI_PID_FILE"
fi

ui_pid="$(tr -d '[:space:]' < "$UI_PID_FILE")"
ui_deadline=$((SECONDS + 30))
while (( SECONDS < ui_deadline )); do
  if ! kill -0 "$ui_pid" 2>/dev/null; then
    echo "OpenKB new UI exited during startup. stderr:" >&2
    tail -60 "$UI_ERR_LOG" >&2 || true
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$UI_URL" 2>/dev/null || true)" == "200" ]]; then
      echo "OpenKB client started: UI $UI_URL (PID $ui_pid), API http://$HOST:$PORT (PID $pid)"
      echo "API logs: $API_OUT_LOG $API_ERR_LOG"
      echo "UI logs: $UI_OUT_LOG $UI_ERR_LOG"
      exit 0
    fi
  elif command -v python3 >/dev/null 2>&1; then
    if python3 - "$UI_HOST" "$UI_PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
with socket.create_connection((host, port), timeout=0.5):
    pass
PY
    then
      echo "OpenKB client started: UI $UI_URL (PID $ui_pid), API http://$HOST:$PORT (PID $pid)"
      echo "API logs: $API_OUT_LOG $API_ERR_LOG"
      echo "UI logs: $UI_OUT_LOG $UI_ERR_LOG"
      exit 0
    fi
  fi
  sleep 0.5
done

echo "OpenKB new UI did not become ready on $UI_URL" >&2
echo "stderr:" >&2
tail -60 "$UI_ERR_LOG" >&2 || true
exit 1

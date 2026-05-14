#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="${OPENKB_CLIENT_HOST:-0.0.0.0}"
PORT="${OPENKB_CLIENT_PORT:-8765}"
UI_HOST="${OPENKB_UI_HOST:-127.0.0.1}"
UI_PORT="${OPENKB_UI_PORT:-3000}"
STATE_DIR="${OPENKB_CLIENT_STATE_DIR:-$REPO_ROOT/.openkb-client}"
API_PID_FILE="$STATE_DIR/client-api-$PORT.pid"
UI_PID_FILE="$STATE_DIR/client-ui-$UI_PORT.pid"

is_openkb_api_pid() {
  local pid="$1"
  local command_line
  command_line="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$command_line" == *openkb* && "$command_line" == *client* ]]
}

is_openkb_ui_pid() {
  local pid="$1"
  local command_line
  command_line="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$command_line" == *next* && "$command_line" == *dev* && "$command_line" == *openkb-new-ui* ]]
}

is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

wait_for_exit() {
  local pid="$1"
  local deadline=$((SECONDS + 10))
  while is_running "$pid" && (( SECONDS < deadline )); do
    sleep 0.2
  done
  ! is_running "$pid"
}

stop_pid() {
  local pid="$1"
  local source="$2"
  local checker="$3"

  if ! is_running "$pid"; then
    return 0
  fi
  if ! "$checker" "$pid"; then
    echo "Refusing to stop non-OpenKB process from $source: PID $pid" >&2
    return 2
  fi

  echo "Stopping OpenKB process PID $pid ($source)"
  kill "$pid" 2>/dev/null || true
  if ! wait_for_exit "$pid"; then
    echo "PID $pid did not exit after SIGTERM; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
}

stopped=0
blocked=0

if [[ -f "$API_PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$API_PID_FILE")"
  if [[ -n "$pid" ]]; then
    stop_pid "$pid" "api pid file" is_openkb_api_pid && stopped=1
  fi
  rm -f "$API_PID_FILE"
fi

if [[ -f "$UI_PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$UI_PID_FILE")"
  if [[ -n "$pid" ]]; then
    stop_pid "$pid" "ui pid file" is_openkb_ui_pid && stopped=1
  fi
  rm -f "$UI_PID_FILE"
fi

api_port_pids=""
if command -v lsof >/dev/null 2>&1; then
  api_port_pids="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
elif command -v fuser >/dev/null 2>&1; then
  api_port_pids="$(fuser "$PORT"/tcp 2>/dev/null || true)"
fi

for pid in $api_port_pids; do
  if is_running "$pid"; then
    if stop_pid "$pid" "api port $HOST:$PORT" is_openkb_api_pid; then
      stopped=1
    else
      blocked=1
    fi
  fi
done

ui_port_pids=""
if command -v lsof >/dev/null 2>&1; then
  ui_port_pids="$(lsof -nP -tiTCP:"$UI_PORT" -sTCP:LISTEN 2>/dev/null || true)"
elif command -v fuser >/dev/null 2>&1; then
  ui_port_pids="$(fuser "$UI_PORT"/tcp 2>/dev/null || true)"
fi

for pid in $ui_port_pids; do
  if is_running "$pid"; then
    if stop_pid "$pid" "ui port $UI_HOST:$UI_PORT" is_openkb_ui_pid; then
      stopped=1
    else
      blocked=1
    fi
  fi
done

if (( blocked != 0 )); then
  echo "OpenKB client was not fully stopped because one managed port is owned by another process." >&2
  exit 2
fi

if (( stopped == 0 )); then
  echo "OpenKB client is not running on API $HOST:$PORT and UI $UI_HOST:$UI_PORT"
else
  echo "OpenKB client stopped on API $HOST:$PORT and UI $UI_HOST:$UI_PORT"
fi

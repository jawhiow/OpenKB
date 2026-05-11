#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="${OPENKB_CLIENT_HOST:-0.0.0.0}"
PORT="${OPENKB_CLIENT_PORT:-8765}"
STATE_DIR="${OPENKB_CLIENT_STATE_DIR:-$REPO_ROOT/.openkb-client}"
PID_FILE="$STATE_DIR/client-$PORT.pid"

is_openkb_client_pid() {
  local pid="$1"
  local command_line
  command_line="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$command_line" == *openkb* && "$command_line" == *client* ]]
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

  if ! is_running "$pid"; then
    return 0
  fi
  if ! is_openkb_client_pid "$pid"; then
    echo "Refusing to stop non-OpenKB process from $source: PID $pid" >&2
    return 2
  fi

  echo "Stopping OpenKB client PID $pid ($source)"
  kill "$pid" 2>/dev/null || true
  if ! wait_for_exit "$pid"; then
    echo "PID $pid did not exit after SIGTERM; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
}

stopped=0
blocked=0

if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ -n "$pid" ]]; then
    stop_pid "$pid" "pid file" && stopped=1
  fi
  rm -f "$PID_FILE"
fi

port_pids=""
if command -v lsof >/dev/null 2>&1; then
  port_pids="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
elif command -v fuser >/dev/null 2>&1; then
  port_pids="$(fuser "$PORT"/tcp 2>/dev/null || true)"
fi

for pid in $port_pids; do
  if is_running "$pid"; then
    if stop_pid "$pid" "port $HOST:$PORT"; then
      stopped=1
    else
      blocked=1
    fi
  fi
done

if (( blocked != 0 )); then
  echo "OpenKB client was not fully stopped because $HOST:$PORT is owned by another process." >&2
  exit 2
fi

if (( stopped == 0 )); then
  echo "OpenKB client is not running on $HOST:$PORT"
else
  echo "OpenKB client stopped on $HOST:$PORT"
fi

#!/usr/bin/env bash
# Restart the trading bot (systemd or plain process).
# Usage: ./scripts/restart.sh
set -euo pipefail

SERVICE_NAME="trading-bot"
PID_FILE="$(dirname "$0")/../bot.pid"

# Prefer systemd when available; fall back to a plain background process.
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  echo "Restarting systemd service: $SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  exit 0
fi

# Plain-process restart: stop the old process if running.
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping old process (PID $OLD_PID)"
    kill "$OLD_PID"
    sleep 2
  fi
  rm -f "$PID_FILE"
fi

cd "$(dirname "$0")/.."
if [[ -d "venv" ]]; then
  source venv/bin/activate
fi

echo "Starting trading bot..."
nohup python main.py >> logs/bot.log 2>&1 &
echo $! > "$PID_FILE"
echo "Started with PID $(cat "$PID_FILE")"

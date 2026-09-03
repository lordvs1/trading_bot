#!/usr/bin/env bash
# Back up the SQLite database and environment configuration.
# Usage: ./scripts/backup_state.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
mkdir -p "$BACKUP_DIR"

if [[ -f "$ROOT/database/trades.db" ]]; then
  cp "$ROOT/database/trades.db" "$BACKUP_DIR/trades_$STAMP.db"
  echo "Backed up database -> $BACKUP_DIR/trades_$STAMP.db"
fi

if [[ -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$BACKUP_DIR/env_$STAMP"
  echo "Backed up .env -> $BACKUP_DIR/env_$STAMP"
fi

echo "Backup complete."

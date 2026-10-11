#!/usr/bin/env bash
# One-shot setup for a fresh Debian/Ubuntu VPS (run as root):
#
#   sudo bash scripts/setup.sh
#
# Installs system packages, (re)creates the virtualenv and installs the Python
# dependencies. Idempotent: safe to re-run.
set -uo pipefail

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# --- must run as root (this script calls apt) ---
if [[ "$(id -u)" -ne 0 ]]; then
  die "Run as root:  sudo bash scripts/setup.sh"
fi

# --- OS check ---
if [[ ! -f /etc/os-release ]]; then
  die "Cannot detect the OS (/etc/os-release missing). This script targets Debian/Ubuntu."
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
  *debian* | *ubuntu*) : ;;
  *) warn "Detected '${ID:-unknown}'; this script targets Debian/Ubuntu. Continuing anyway." ;;
esac

# --- project root = parent of this script ---
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
log "Project root: $ROOT"

# --- system packages ---
log "Installing system packages (git, python3, python3-venv, python3-pip, build tools)..."
apt-get update -y || die "apt-get update failed"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git python3 python3-venv python3-pip python3-dev build-essential \
  || die "apt-get install failed"

PY="$(command -v python3)" || die "python3 not found after install"
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Using Python $PYVER ($PY)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' \
  || die "Python >= 3.10 is required, found $PYVER"

# --- (re)create the virtual environment ---
if [[ -d venv ]]; then
  log "Removing existing venv/ ..."
  rm -rf venv
fi
log "Creating virtual environment (venv)..."
if ! "$PY" -m venv venv; then
  die "python3 -m venv failed. On Debian/Ubuntu install the venv package, then retry:
       apt-get install -y python3-venv python3.${PYVER#3.}-venv
       rm -rf venv"
fi

# --- Python dependencies ---
log "Installing Python dependencies (may take a few minutes)..."
./venv/bin/pip install --upgrade pip wheel || die "pip upgrade failed"
if ! ./venv/bin/pip install -r requirements.txt; then
  warn "Dependency install failed."
  warn "If pip is building pandas/numpy from source, your Python ($PYVER) may not have prebuilt wheels."
  warn "Fallback using uv (installs a standalone Python 3.12, no apt needed):"
  warn "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  warn "  uv venv --python 3.12 && uv pip install -r requirements.txt"
  die "pip install -r requirements.txt failed"
fi

# --- verify ---
log "Verifying imports..."
./venv/bin/python -c "import pandas, numpy, pybit, telegram, dotenv; print('OK')" \
  || die "Import check failed"

cat <<'EOF'

Setup complete.

Next steps:
  1) cp .env.example .env
  2) edit .env   ->  TRADE_MODE=paper, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                     INITIAL_BALANCE, STRATEGY
  3) ./venv/bin/python main.py     # test run; a "Bot started" message should arrive in Telegram
  4) For autostart add the systemd unit (see README -> "Deployment on VPS").

Quick tests:
  ./venv/bin/python -m pytest tests/ -q
EOF

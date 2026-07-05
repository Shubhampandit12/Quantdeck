#!/usr/bin/env bash
# Start the QuantDeck backend API (creates the venv + installs deps on first run).
set -euo pipefail
cd "$(dirname "$0")/../backend"

if [ ! -d .venv ]; then
  echo "Creating virtualenv + installing dependencies..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi

[ -f .env ] || cp .env.example .env

exec ./.venv/bin/python -m uvicorn quantdeck.api.app:create_app --factory \
  --host "${QUANTDECK_HOST:-0.0.0.0}" --port "${QUANTDECK_PORT:-8000}"

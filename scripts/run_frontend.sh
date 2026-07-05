#!/usr/bin/env bash
# Start the QuantDeck frontend dev server (installs deps on first run).
set -euo pipefail
cd "$(dirname "$0")/../frontend"

[ -d node_modules ] || npm install
exec npm run dev

#!/usr/bin/env bash
# Start the QuantDeck MCP server (connects Claude to your paper account).
# The QuantDeck API must be running first (scripts/run_backend.sh).
#
# For Claude Desktop / Claude Code you normally DON'T run this by hand — you
# point the client at it via config (see docs/MCP.md). This script is for a
# quick manual sanity check and to print the ready-to-paste config.
set -euo pipefail
cd "$(dirname "$0")/../backend"

export QUANTDECK_API_URL="${QUANTDECK_API_URL:-http://localhost:8000}"

# Print the exact client config with the current default portfolio filled in.
PID="$(curl -s "$QUANTDECK_API_URL/api/health" 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("default_portfolio_id",""))' 2>/dev/null || true)"

cat <<EOF
QuantDeck MCP server
--------------------
API_URL         : $QUANTDECK_API_URL
Default portfolio: ${PID:-<start the API first>}

Claude Desktop config (claude_desktop_config.json):

{
  "mcpServers": {
    "quantdeck": {
      "command": "$(pwd)/.venv/bin/python",
      "args": ["-m", "quantdeck.mcp_server"],
      "env": {
        "QUANTDECK_API_URL": "$QUANTDECK_API_URL",
        "QUANTDECK_PORTFOLIO_ID": "${PID}"
      }
    }
  }
}

Starting the server on stdio now (Ctrl-C to stop)...
EOF

exec ./.venv/bin/python -m quantdeck.mcp_server

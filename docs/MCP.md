# Connecting an AI via MCP

QuantDeck ships a standalone **MCP (Model Context Protocol) stdio server** that
exposes the whole trading toolbox to any MCP client (Claude Desktop, Claude Code,
or a custom agent). The AI can then read markets and trade your **paper** account
from a plain-English strategy.

Every MCP tool call is proxied over HTTP to the **running QuantDeck REST API**, so
the AI goes through the exact same shared risk-check path as the UI — it can
never bypass it, and it can never touch real money.

## Tools exposed

`get_quote`, `get_ohlcv`, `search_symbols`, `get_portfolio`, `get_positions`,
`get_open_orders`, `get_trade_history`, `place_order`, `cancel_order`,
`modify_order`, `run_backtest`, `get_market_summary`.

## Setup

1. **Start the API** (it must be running for the MCP tools to work):

   ```bash
   cd backend && source .venv/bin/activate
   python -m uvicorn quantdeck.api.app:create_app --factory --port 8000
   ```

2. **Find a portfolio id** (optional — tools accept a `portfolio_id` arg too):

   ```bash
   curl -s http://localhost:8000/api/health | python -m json.tool   # default_portfolio_id
   ```

3. **Register the server** with your MCP client. The easiest way to get the
   exact config (with your portfolio id and venv path filled in) is:

   ```bash
   ./scripts/run_mcp.sh      # prints the config, then serves on stdio
   ```

   For Claude Desktop, paste that into `claude_desktop_config.json`. It looks
   like this — **use the venv's python** so the `quantdeck` package resolves
   without any `PYTHONPATH` setup:

   ```json
   {
     "mcpServers": {
       "quantdeck": {
         "command": "/ABSOLUTE/PATH/TO/QuantDeck/backend/.venv/bin/python",
         "args": ["-m", "quantdeck.mcp_server"],
         "env": {
           "QUANTDECK_API_URL": "http://localhost:8000",
           "QUANTDECK_PORTFOLIO_ID": "<your-portfolio-id>"
         }
       }
     }
   }
   ```

   Then fully restart Claude Desktop. You'll see a "quantdeck" tool group with
   12 tools. Verified working end-to-end: an MCP client can list the tools, pull
   live quotes, and place paper orders that flow through the same risk-checked
   engine as the UI.

4. **Talk to the AI.** Give it your strategy, e.g.:

   > "Using QuantDeck, trade BTC/USDT and ETH/USDT. Go long when the 20-period
   > RSI on the 15m chart drops below 30, exit at RSI 55 or a 3% stop. Never risk
   > more than 2% of the portfolio per trade. Show me each order."

   The AI will call `get_ohlcv`, reason, and call `place_order` on your paper
   account.

## In-app alternative

You don't need MCP to use the AI — the terminal's **AI Session** panel runs the
same autonomous loop in-app (with guardrails + kill-switch) and the command bar
does one-off natural-language orders. MCP is for driving it from an external
Claude client. Both require `ANTHROPIC_API_KEY` for the reasoning model; the MCP
tools themselves only need the API running.

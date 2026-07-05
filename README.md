# QuantDeck — AI-Driven Paper Trading Platform

A TradingView-style, **demo-money-only** trading platform with deep support for
**AI-driven** and **code-driven** trading. Watch live crypto and US-stock markets
on rich charts, hand an AI a plain-English strategy and let it trade your paper
account via MCP, ask the AI for one-off manual trades, or inject your own Python
strategy to backtest and run live.

> **No real money, ever.** There is no brokerage integration and no code path,
> config flag, or credential that can route an order to a real exchange. The only
> external credentials used are *read-only* market-data keys and an optional
> Anthropic API key for the AI features. Every balance, order, and P&L is
> simulated.

---

## What it does

- **Two asset classes:** Crypto (Binance, real-time) and US equities incl. the
  full **S&P 500** (real-time via Alpaca/Finnhub if keyed, else 15-min delayed
  via Yahoo). Data is always labeled with its source and delay — never fabricated.
- **Paper-trading engine:** market / limit / stop / stop-limit / bracket orders,
  long & short, realized/unrealized P&L, **customizable starting balance**, and
  **leverage** with margin, buying-power, margin-call and liquidation simulation.
- **AI auto-trading via MCP:** an MCP server exposes the whole trading surface so
  Claude (Desktop / Code / any MCP client) can trade your paper account from your
  strategy prompt.
- **AI manual trading:** natural-language one-off orders ("buy 10 AAPL").
- **Code-injected strategies:** write a Python `Strategy`, run it live in a
  **sandbox**, or backtest the identical file.
- **Backtesting:** replays history through the same engine; report leads with
  **total profit/loss in $ and %**, plus equity curve, drawdown, Sharpe, win
  rate, and a trade log.
- **TradingView-style UI:** a home/landing page and a full terminal — live
  candlestick chart, watchlist, order ticket with leverage, portfolio/positions,
  and the **complete drawing-tools set** (trend lines, rays, Fibonacci, shapes,
  measures, long/short tools, text…). Chart indicators are intentionally out of
  scope.

## Architecture

```
market data  ─►  simulation engine  ─►  ┌─ REST + WebSocket API ─► React SPA
(Binance/Yahoo/   (orders, fills,        ├─ MCP server ─► Claude / MCP clients
 Finnhub/Alpaca)   positions, P&L,       └─ strategy runtime (sandbox) + backtester
                   leverage/margin,
                   shared risk checks)
```

The engine is the single source of truth. The API, the MCP tools, and user
strategies are three clients of the *same* engine and the *same* risk-check path.

- Backend: **Python 3.12 + FastAPI** (async), SQLAlchemy + SQLite, `httpx`,
  `websockets`, `yfinance`, `anthropic`, `mcp`.
- Frontend: **React 18 + TypeScript + Vite + lightweight-charts + zustand**.

See `docs/SPINE.md` (module map), `docs/API_CONTRACT.md` (REST/WS contract),
`docs/FRONTEND_SPEC.md`, and `docs/STRATEGIES.md` (how to write strategies).

---

## Quick start

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add ANTHROPIC_API_KEY / data keys
python -m uvicorn quantdeck.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

Runs fully offline for crypto (Binance is public). US-equity real-time needs a
free Finnhub/Alpaca key; otherwise it falls back to Yahoo (15-min delayed).

### 2. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api and /ws to :8000)
```

Open http://localhost:5173 — the home page opens into the terminal.

### 3. Run the tests

```bash
cd backend && source .venv/bin/activate
pytest -q
```

---

## AI trading

The AI features activate when an Anthropic API key is present (set
`ANTHROPIC_API_KEY` in the environment or `QUANTDECK_ANTHROPIC_API_KEY` in
`backend/.env`). Without a key, the UI still renders and the API returns an
honest "AI disabled" response.

- **In-app:** the terminal's *AI Session* panel takes a plain-English strategy
  prompt + guardrails (allowed symbols, max order size, order rate, max daily
  loss) and trades autonomously, streaming its reasoning and every tool call.
  A kill-switch / flatten-all is always available. The command bar does one-off
  NL orders.
- **Via MCP (Claude Desktop / Code):** see `docs/MCP.md`. Start the API, then
  register the MCP server; Claude can then call `place_order`, `get_quote`,
  `run_backtest`, etc. against your paper account.

## Safety model

1. No brokerage/exchange order APIs exist in the codebase — only market-data reads.
2. Injected strategy code is AST-scanned and executed in a resource-limited
   subprocess sandbox with no filesystem/network access beyond the `ctx` API.
3. Data is labeled with source + delay; unavailable data is shown honestly,
   never fabricated.
4. All orders — UI, AI, or strategy — pass the same validation + risk checks.

## Project layout

```
backend/
  quantdeck/
    core/         canonical models, config, event bus, errors, symbols
    db/           async SQLAlchemy models + session
    marketdata/   provider adapters + MarketDataService (+ S&P 500 loader)
    engine/       TradingEngine, accounting, matching, persistence
    strategies/   Strategy SDK, loader, subprocess sandbox, live runner
    backtest/     Backtester (same SDK, historical replay)
    ai/           MCP tool schema + dispatch, AI session manager
    api/          FastAPI app, routers, WebSockets
    mcp_server/   standalone MCP stdio server
  strategies_examples/   sma_crossover, rsi_reversion, buy_and_hold
  tests/          pytest suite
frontend/
  src/
    api/          typed REST client + WS helpers + shared types
    pages/        HomePage, TerminalPage, StrategiesPage, BacktestPage
    components/   terminal/*, drawing/*, StrategyManager, BacktestPanel, AISessionPanel
    state/        zustand store
docs/             SPINE, API_CONTRACT, FRONTEND_SPEC, STRATEGIES, MCP
```

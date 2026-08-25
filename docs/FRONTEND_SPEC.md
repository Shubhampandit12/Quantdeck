# QuantDeck Frontend Build Spec (FROZEN base)

Stack: **React 18 + TypeScript + Vite + react-router-dom + zustand +
lightweight-charts**. Dark, TradingView-inspired. Files under `frontend/src`.

## Frozen base — DO NOT modify, only import:
- `src/api/types.ts` — all data types (mirror of backend contract).
- `src/api/client.ts` — `api.*` typed REST calls (one per endpoint).
- `src/api/ws.ts` — `openMarketSocket(symbols, onQuote)`, `openEventSocket(portfolioId, onEvent)`.
- `src/state/store.ts` — `useStore()` zustand store (portfolios, activePortfolioId, snapshot, selectedSymbol, quotes; actions loadPortfolios/setActivePortfolio/refreshSnapshot/setSelectedSymbol/applyQuote).
- `src/styles/theme.css` — CSS variables + primitives (.btn, .btn-primary, .btn-buy, .btn-sell, .input, .badge, .badge-demo, .badge-rt, .badge-delayed, .card, .panel-title). USE THESE tokens; don't hardcode colors.

## Routing (react-router-dom)
- `/` → `HomePage` (TradingView-style landing).
- `/terminal/:symbol?` → `TerminalPage` (full trading terminal). Default symbol AAPL.

## Currency / number formatting
Provide `src/lib/format.ts`: `fmtUsd(n)`, `fmtNum(n, dp)`, `fmtPct(n)`, `fmtSigned(n)`.
Green for >=0, red for <0 using `.up`/`.down`.

## Components to build (each its own file under src/components or src/pages)

### HomePage (`src/pages/HomePage.tsx` + `HomePage.css`)
Resembles TradingView home:
- Top nav bar: logo "QuantDeck", nav links (Markets / Charts / Strategies / Backtest), a symbol search box (calls api.search, navigates to /terminal/:symbol), and a permanent `PAPER / DEMO MONEY` badge (.badge-demo).
- Hero: bold headline (e.g. "Trade the markets with AI — with zero real-money risk"), subtext, big CTA button "Open the trading terminal" → /terminal/AAPL.
- Market overview: a scrolling ticker tape of major symbols (AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META, SPY...) with live price + change% (subscribe via openMarketSocket; seed with api.quotes). Mini sparkline optional (can fetch small ohlcv).
- Movers: "Top Gainers / Top Losers" cards from api.marketSummary().
- Clicking any symbol anywhere → navigate to /terminal/:symbol.
- No watchlist preview and no features grid on this page — the terminal (`/terminal`) and backtest (`/backtest`) CTAs above are the only other entry points.

### TerminalPage (`src/pages/TerminalPage.tsx` + css) — the full terminal

The manual UI here is deliberately minimal — QuantDeck's point is the AI/MCP
path, not a full manual brokerage terminal. The engine, `api.setLeverage`,
the MCP `place_order` tool, and the Python Strategy SDK all still support
limit/stop orders, leverage, and TP/SL unchanged; only this manual form and
its TopBar controls are trimmed. Single portfolio only — the app auto-creates
one "Default" portfolio on startup (`api/deps.py::_ensure_default_portfolio`),
so there's no portfolio switcher or "New" button.

Grid layout (TradingView-like):
- TopBar (own component): timeframe selector (1m/5m/15m/1h/4h/1d/1w), "Reset" (clears positions/orders, resets cash), balance/equity display (click Cash to change the demo balance), connection status dot, PAPER/DEMO badge. No portfolio switcher, no "New", no leverage editor, no top-bar symbol search (use the Watchlist's own filter).
- Left panel: `Watchlist` component showing the fixed 15-symbol demo universe from api.symbols('us_equity') (`marketdata/sp500.py::DEMO_SYMBOLS`), searchable, live prices; click selects symbol.
- Center: `ChartPanel` — lightweight-charts candlestick, loads api.ohlcv on symbol/interval change, updates last candle from live quotes (openMarketSocket). Shows data source + delay label (badge-rt / badge-delayed from quote.source/quote.delay). Read-only: zoom (scroll/pinch) and pan (drag) via handleScroll/handleScale, but no drawing tools — there is no toolbar and nothing on the chart is user-editable.
- Right panel: `OrderTicket` — side (Buy/Long, Sell/Short) + quantity only, always a market order, submit → api.placeOrder; shows buying power and margin used, plus the real open position for the selected symbol (qty, avg entry, last, market value, uPnL, liq. price) when one exists. Below it a compact `PortfolioSummary` (cash, equity, unrealized pnl, realized pnl).
- Bottom panel: tabbed `Positions | Open Orders | AI Session`. Positions table (symbol, side, qty, avg, last, uPnL, liq price, close btn). Open orders (cancel btn). AI session trace stream. (No Trade History or Strategy Logs tabs in this UI.)
- Command bar (bottom): a single input that does natural-language order entry / AI chat → api.aiChat; render the reply + trace.
- Live updates: subscribe openEventSocket(activePortfolioId) → on order/position/portfolio events call store.refreshSnapshot() (debounced).

### StrategyManager (`src/components/StrategyManager.tsx`) + BacktestPanel
- StrategyManager: list strategies (api.strategies), a Monaco-free code editor (a styled <textarea> with monospace + line highlighting is fine), create/edit/save/delete, start/stop against the active portfolio, live status + logs (via strategyLogs poll or events).
- BacktestPanel: form (symbols, interval, date range, starting_balance, leverage, commission_bps, slippage_bps, code or strategy select) → api.backtest. Render result LEADING with big total P&L ($ and %) and ending balance, then an equity-curve chart (lightweight-charts line series), key metrics grid (return, max DD, Sharpe, win rate, #trades, profit factor), and a trades table. Show data_warnings prominently. These can live on the terminal (a tab/modal) or dedicated routes /strategies and /backtest — your choice, but reachable from the top nav.

### AISessionPanel (`src/components/AISessionPanel.tsx`)
Textarea for the natural-language strategy prompt + guardrails (allowed symbols, max order notional, max orders/min, max daily loss), Start/Pause/Resume/Stop buttons, and a live scrolling trace of the AI's thoughts / tool calls / results (from openEventSocket ai_trace events or polling api.aiSession). Always-visible "Flatten all / kill switch" button → api.flatten.

## Quality bar
- Must `npm run typecheck` clean (tsc --noEmit). Use the exact types from api/types.ts.
- No hardcoded colors — use theme tokens.
- Smooth/lag-free: update the live candle in place, don't refetch the whole series on each tick.
- Everything reachable from the home page; the terminal is the heart.

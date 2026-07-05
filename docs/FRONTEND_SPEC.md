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
- `/terminal/:symbol?` → `TerminalPage` (full trading terminal). Default symbol BTC/USDT.

## Currency / number formatting
Provide `src/lib/format.ts`: `fmtUsd(n)`, `fmtNum(n, dp)`, `fmtPct(n)`, `fmtSigned(n)`.
Green for >=0, red for <0 using `.up`/`.down`.

## Components to build (each its own file under src/components or src/pages)

### HomePage (`src/pages/HomePage.tsx` + `HomePage.css`)
Resembles TradingView home:
- Top nav bar: logo "QuantDeck", nav links (Markets / Charts / Strategies / Backtest), a symbol search box (calls api.search, navigates to /terminal/:symbol), and a permanent `PAPER / DEMO MONEY` badge (.badge-demo).
- Hero: bold headline (e.g. "Trade the markets with AI — with zero real-money risk"), subtext, big CTA button "Open the trading terminal" → /terminal/BTC%2FUSDT.
- Market overview: a scrolling ticker tape of major symbols (BTC/USDT, ETH/USDT, AAPL, MSFT, NVDA, TSLA, SPY...) with live price + change% (subscribe via openMarketSocket; seed with api.quotes). Mini sparkline optional (can fetch small ohlcv).
- Movers: "Top Gainers / Top Losers" cards from api.marketSummary().
- Watchlist preview + a features section (AI trading, code backtesting, paper portfolios, leverage) as cards.
- Clicking any symbol anywhere → navigate to /terminal/:symbol.

### TerminalPage (`src/pages/TerminalPage.tsx` + css) — the full terminal
Grid layout (TradingView-like):
- TopBar (own component): symbol search, asset-class filter (US Stocks / Crypto), timeframe selector (1m/5m/15m/1h/4h/1d/1w), portfolio switcher (dropdown of portfolios + "New" + "Reset"), leverage display + editor, balance/equity display, connection status dot, PAPER/DEMO badge.
- Left panel: `Watchlist` component with tabs (US Stocks shows S&P500 from api.symbols('us_equity'); Crypto from api.symbols('crypto')), searchable, live prices; click selects symbol.
- Center: `ChartPanel` — lightweight-charts candlestick, loads api.ohlcv on symbol/interval change, updates last candle from live quotes (openMarketSocket). Shows data source + delay label (badge-rt / badge-delayed from quote.source/quote.delay). Includes the DrawingToolbar + drawing overlay (see below).
- Right panel: `OrderTicket` — side (buy/sell), type (market/limit/stop/stop_limit), qty, price/stop fields, leverage selector, TP/SL, submit → api.placeOrder; show buying power, margin used, and estimated liquidation. Below it a compact `PortfolioSummary` (cash, equity, unrealized pnl, realized pnl).
- Bottom panel: tabbed `Positions | Open Orders | Trade History | Strategy Logs | AI Session`. Positions table (symbol, side, qty, avg, last, uPnL, liq price, close btn). Open orders (cancel btn). History (fills). Strategy logs stream. AI session trace stream.
- Command bar (bottom): a single input that does natural-language order entry / AI chat → api.aiChat; render the reply + trace.
- Live updates: subscribe openEventSocket(activePortfolioId) → on order/position/portfolio events call store.refreshSnapshot() (debounced) and append to logs/trace.

### DrawingToolbar + overlay (`src/components/drawing/`)
Left-edge vertical toolbar over the chart with the FULL TradingView-style tool set:
cursor/crosshair, trend line, ray, extended line, horizontal line, horizontal ray,
vertical line, parallel channel, Fibonacci retracement, Fibonacci extension, Fib fan,
rectangle, ellipse, triangle, brush/freehand, highlighter, arrow, price-range ruler,
date-range ruler, long position, short position, text/callout note, and a trash/"remove all".
Implement drawings as an SVG (or canvas) overlay aligned to the lightweight-charts
time/price scales (use chart.timeScale() and series.priceToCoordinate / coordinateToPrice,
and timeScale coordinate conversions; re-render on 'timeScale visibleLogicalRangeChange'
and crosshair move). Drawings: click-drag to create; selectable, movable, editable
(color, thickness, style via a small popover); persist per symbol in localStorage
(key `qd:drawings:<symbol>`); delete individually (select+Del) or all. Fibonacci draws
levels 0/0.236/0.382/0.5/0.618/0.786/1 (+ extensions 1.272/1.618 for extension tool).
Keep it modular: a `tools.ts` registry, a `DrawingLayer.tsx` renderer, a `useDrawings` hook.
(NOTE: chart technical INDICATORS are intentionally OUT OF SCOPE — do not build them.)

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

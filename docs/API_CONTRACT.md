# QuantDeck API Contract (FROZEN)

Base URL: `http://localhost:8000`. All REST endpoints are under `/api`. All
bodies are JSON. Timestamps are ISO-8601 UTC. **This contract is frozen** — the
backend and frontend are built in parallel against it.

> Safety invariant surfaced everywhere: this is **demo money only**. No endpoint
> can move real funds. Quotes carry a `source` and `delay` so the UI can label
> data honestly.

## Canonical shapes

See `frontend/src/api/types.ts` for the TypeScript mirror of every shape below,
and `backend/quantdeck/core/models.py` for the Pydantic source of truth. Enum
string values are exactly as in `core/enums.py`.

- `AssetClass`: `"us_equity" | "crypto"`
- `OrderSide`: `"buy" | "sell"`
- `OrderType`: `"market" | "limit" | "stop" | "stop_limit"`
- `OrderStatus`: `"pending" | "open" | "partially_filled" | "filled" | "cancelled" | "rejected" | "expired"`
- `PositionSide`: `"long" | "short" | "flat"`
- `DataDelay`: `"realtime" | "delayed_15m" | "unknown"`

## Market data

- `GET /api/symbols?asset_class=us_equity|crypto` → `SymbolInfo[]`
- `GET /api/search?q=<query>&limit=25` → `SymbolInfo[]`
- `GET /api/quote/{symbol}` → `Quote`  (symbol may be URL-encoded, e.g. `BTC%2FUSDT`)
- `GET /api/quotes?symbols=AAPL,MSFT,BTC/USDT` → `Quote[]`
- `GET /api/ohlcv/{symbol}?interval=1d&limit=500&start=&end=` → `OHLCVResponse`
- `GET /api/market/summary` → `{ movers_up: Quote[], movers_down: Quote[], crypto: Quote[], stocks: Quote[] }`

## Portfolios

- `GET /api/portfolios` → `Portfolio[]`
- `POST /api/portfolios` body `{ name, starting_balance, leverage, fill_mode }` → `Portfolio`
- `GET /api/portfolios/{id}` → `Portfolio`
- `GET /api/portfolios/{id}/snapshot` → `PortfolioSnapshot` `{ portfolio, positions, open_orders }`
- `POST /api/portfolios/{id}/reset` body `{ starting_balance? }` → `Portfolio`
- `POST /api/portfolios/{id}/leverage` body `{ leverage }` → `Portfolio`
- `POST /api/portfolios/{id}/risk` body `RiskLimits` → `Portfolio`
- `GET /api/portfolios/{id}/positions` → `Position[]`
- `GET /api/portfolios/{id}/orders?open=true` → `Order[]`
- `GET /api/portfolios/{id}/fills?limit=200` → `Fill[]`

## Orders

- `POST /api/portfolios/{id}/orders` body `OrderRequest` → `Order`
  (result may be `status:"rejected"` with `reject_reason`; HTTP is still 200)
- `POST /api/portfolios/{id}/orders/{order_id}/cancel` → `Order`
- `POST /api/portfolios/{id}/orders/{order_id}/modify` body partial → `Order`
- `POST /api/portfolios/{id}/flatten` → `Order[]`  (kill-switch: close everything)

`OrderRequest` = `{ symbol, side, type, quantity, limit_price?, stop_price?, take_profit?, stop_loss?, time_in_force?, source? }`

## Strategies (code injection)

- `GET /api/strategies` → `StrategySummary[]` `{ id, name, status, portfolio_id }`
- `POST /api/strategies` body `{ name, code, portfolio_id?, params? }` → `StrategySummary`
- `GET /api/strategies/{id}` → `{ id, name, code, status, portfolio_id, params }`
- `PUT /api/strategies/{id}` body `{ name?, code?, params? }` → `StrategySummary`
- `DELETE /api/strategies/{id}` → `{ ok: true }`
- `POST /api/strategies/{id}/start` body `{ portfolio_id, symbols?, interval? }` → `StrategySummary`
- `POST /api/strategies/{id}/stop` → `StrategySummary`
- `GET /api/strategies/{id}/logs?limit=200` → `{ logs: string[] }`

## Backtesting

- `POST /api/backtest` body `BacktestRequest` → `BacktestResult`
  - `BacktestRequest` = `{ code?, strategy_id?, symbols[], interval, start?, end?, starting_balance, leverage, fill_mode, commission_bps, slippage_bps, params? }`
  - `BacktestResult` leads with `total_pnl`, `total_return_pct`, `ending_balance`, then `equity_curve[]`, `trades[]`, metrics, `logs[]`, `data_warnings[]`.

## AI trading

- `GET /api/ai/status` → `{ enabled: boolean, model: string }`
- `POST /api/ai/chat` body `{ portfolio_id, message }` → `{ reply: string, trace: AITraceStep[] }`  (manual one-off / NL order)
- `POST /api/ai/session/start` body `{ portfolio_id, strategy_prompt, guardrails? }` → `{ session_id, status }`
- `POST /api/ai/session/{session_id}/pause` → `{ status }`
- `POST /api/ai/session/{session_id}/resume` → `{ status }`
- `POST /api/ai/session/{session_id}/stop` → `{ status }`
- `GET /api/ai/session/{session_id}` → `{ session_id, status, trace: AITraceStep[] }`
  - guardrails = `{ allowed_symbols?: string[], max_order_notional?, max_orders_per_min?, max_daily_loss? }`
  - `AITraceStep` = `{ kind: "thought"|"tool_call"|"tool_result"|"message", text?, tool?, args?, result?, timestamp }`

## WebSockets

- `WS /ws/market?symbols=AAPL,BTC/USDT` → streams `{ type:"quote", data: Quote }` frames.
  Client may send `{ "action":"subscribe"|"unsubscribe", "symbols":[...] }` to change subscriptions.
- `WS /ws/events?portfolio_id=<id>` → streams `Event` frames `{ type, portfolio_id, timestamp, data }`
  for order/position/portfolio/margin/liquidation updates, plus `strategy_log`,
  `strategy_status`, `ai_trace`, `ai_status`.

## Health

- `GET /api/health` → `{ status:"ok", version, ai_enabled, providers: {...} }`

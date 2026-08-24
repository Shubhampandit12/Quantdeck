# QuantDeck Spine — the frozen integration contract

Every backend module is built against these files. **Do not modify spine files.**
Import from them; do not redefine their types.

## Layout
```
backend/quantdeck/
  core/
    enums.py       # AssetClass, OrderSide, OrderType, OrderStatus, PositionSide,
                   # TimeInForce, FillMode, DataDelay, EventType, StrategyStatus,
                   # AISessionStatus, RunMode
    models.py      # Pydantic: SymbolInfo, Quote, Bar, OHLCVResponse, OrderRequest,
                   # Order, Fill, Position, Portfolio, PortfolioSnapshot,
                   # RiskLimits, Event, BacktestRequest, BacktestResult,
                   # TradeRecord, EquityPoint ; helper utcnow()
    config.py      # settings singleton (get_settings())
    eventbus.py    # `bus` singleton: await bus.publish(Event), bus.publish_soon(e),
                   # async for e in bus.subscribe(portfolio_id=, types=)
    errors.py      # QuantDeckError + subclasses (DataUnavailableError,
                   # OrderRejected, InsufficientBuyingPower, RiskLimitExceeded,...)
    symbols.py     # normalize_symbol(), classify()->AssetClass
  db/
    database.py    # Base, engine, SessionLocal, init_db(), get_session()
    models.py      # PortfolioORM, PositionORM, OrderORM, FillORM, StrategyORM
  marketdata/
    base.py        # MarketDataProvider (ABC), MarketData (Protocol), VALID_INTERVALS
    service.py     # << BUILD: MarketDataService + get_market_data() singleton >>
    providers/     # << BUILD: yahoo.py, finnhub.py, alpaca.py >>
    sp500.py       # << BUILD: S&P 500 constituent list loader >>
  engine/
    interface.py   # EngineProtocol (frozen public surface)
    engine.py      # << BUILD: TradingEngine implementing EngineProtocol >>
    matching.py    # << BUILD: order matching / fill model helpers (optional split) >>
    accounting.py  # << BUILD: position/P&L/margin math (optional split) >>
  strategies/
    sdk.py         # Strategy base class, StrategyContext protocol (frozen)
    context.py     # << BUILD: LiveStrategyContext bound to engine+marketdata >>
    sandbox.py     # << BUILD: safe subprocess runner for user code >>
    runner.py      # << BUILD: StrategyManager: start/stop/list, drives live ctx >>
    loader.py      # << BUILD: safely load a Strategy subclass from source code >>
  backtest/
    engine.py      # << BUILD: Backtester + BacktestContext (uses Strategy SDK) >>
  ai/
    tools.py       # << BUILD: tool schema + dispatch to engine/marketdata >>
    session.py     # << BUILD: AISessionManager (autonomous loop + manual chat) >>
  api/
    app.py         # << BUILD: create_app(), lifespan, singletons wiring >>
    deps.py        # << BUILD: shared dependency accessors >>
    routes_*.py    # << BUILD: market, portfolio, order, strategy, backtest, ai >>
    ws.py          # << BUILD: /ws/market and /ws/events >>
  mcp_server/
    server.py      # << BUILD: standalone MCP server exposing trading tools >>
```

## Key rules
1. **Async everywhere** on the backend. Engine + market-data methods are `async`.
2. **Never fabricate prices.** On provider failure raise `DataUnavailableError`;
   the API turns it into an honest "unavailable" response.
3. **All orders go through `engine.place_order()`** — UI, AI, and strategies. That
   is the single risk-check path. Returning a `REJECTED` Order is correct; do not
   raise for business rejections (raise only for truly exceptional states).
4. **Emit events** via `bus.publish_soon(Event(...))` on every state change so the
   WS layer and strategies see them.
5. **No real money.** No broker/exchange order APIs. Market-data + AI keys only.

## The MarketData surface (consumers call these)
```python
md = get_market_data()
q: Quote = await md.get_quote("AAPL")
price: float = await md.get_price("AAPL")
ohlcv: OHLCVResponse = await md.get_ohlcv("AAPL", "1d", limit=500)
results: list[SymbolInfo] = await md.search_symbols("app")
syms: list[SymbolInfo] = await md.get_symbols(AssetClass.US_EQUITY)  # includes S&P500
async for quote in md.subscribe(["AAPL"]): ...
```

## The Engine surface (consumers call these) — see engine/interface.py
Construct once: `TradingEngine(market_data=md)`; expose via api/deps and mcp.
Place orders with `OrderRequest`; get state with `get_snapshot(portfolio_id)`.

## The Strategy surface — see strategies/sdk.py
User code subclasses `Strategy`, uses `ctx` (a `StrategyContext`). Live and
backtest both provide a `StrategyContext`, so the same file runs in both.

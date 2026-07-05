"""Anthropic tool-use surface for the QuantDeck trading agent.

This module is the single source of truth for the *set* of actions an AI agent
can take. It exposes two things:

* ``tool_specs()`` — the Anthropic tool-use JSON schema for every trading tool.
  The MCP server (``mcp_server/server.py``) reuses these exact names,
  descriptions, and input schemas so the LLM sees an identical toolbox whether
  it is driven in-process (``ai/session.py``) or over stdio via MCP.

* ``dispatch(name, args, ...)`` — executes a tool call against the live engine,
  market-data service, and backtester. Every mutation flows through the same
  objects the REST API and strategy runtime use, so the AI cannot bypass the
  shared risk-check path.

Safety notes:
  * ``place_order`` builds an :class:`OrderRequest` with ``source="ai"`` and
    calls ``engine.place_order`` — the one and only order entry point. A
    business rejection comes back as a ``REJECTED`` Order (not an exception),
    which we serialize honestly so the model can react.
  * We never fabricate prices. If market data is unavailable the underlying
    service raises :class:`DataUnavailableError`; we translate that into an
    ``{"error": ...}`` payload so the loop degrades gracefully instead of
    inventing numbers.
  * All return values are plain, JSON-serializable dicts (Pydantic models are
    dumped via ``model_dump(mode="json")``).
"""
from __future__ import annotations

from typing import Any

from ..core.enums import AssetClass, OrderSide, OrderType, TimeInForce
from ..core.errors import DataUnavailableError, QuantDeckError
from ..core.models import BacktestRequest, OrderRequest

# --------------------------------------------------------------------------- #
# Tool schemas                                                                #
# --------------------------------------------------------------------------- #
# The names here are load-bearing: they are referenced by the dispatcher below
# and re-exported verbatim by the MCP server. Descriptions are written for the
# model, not for humans — they nudge the agent toward safe, precise behavior.


def tool_specs() -> list[dict]:
    """Return the Anthropic tool-use schema list for all trading tools.

    The shape of each entry matches the Anthropic Messages API ``tools`` field:
    ``{"name", "description", "input_schema"}``. The MCP server maps the same
    fields onto ``mcp.types.Tool`` (``inputSchema``).
    """
    return [
        {
            "name": "get_quote",
            "description": (
                "Get the latest normalized quote (price, bid/ask, change, data "
                "source and delay) for one symbol. Symbols: US equities like "
                "'AAPL' or crypto pairs like 'BTC/USDT'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "e.g. 'AAPL' or 'BTC/USDT'"},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "get_ohlcv",
            "description": (
                "Get historical OHLCV candles for a symbol. Use this to reason "
                "about trends, support/resistance, momentum, and volatility "
                "before deciding to trade."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "interval": {
                        "type": "string",
                        "description": "One of 1m,5m,15m,30m,1h,4h,1d,1w",
                        "default": "1d",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of most-recent bars (default 100).",
                        "default": 100,
                    },
                    "start": {"type": "string", "description": "ISO start date (optional)."},
                    "end": {"type": "string", "description": "ISO end date (optional)."},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "search_symbols",
            "description": (
                "Search tradable instruments by name or ticker fragment. Use "
                "when the user names a company or coin loosely."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_portfolio",
            "description": (
                "Get the current paper-trading portfolio: cash, equity, buying "
                "power, leverage, realized/unrealized P&L. DEMO MONEY ONLY."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_positions",
            "description": "List all open positions with size, entry, mark price and P&L.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_open_orders",
            "description": "List all working (open/pending) orders on the portfolio.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_trade_history",
            "description": "List recent fills (executed trades) for the portfolio.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "place_order",
            "description": (
                "Place a paper-trading order. Goes through the shared risk-check "
                "path; the result may come back REJECTED with a reject_reason "
                "(that is a normal, non-fatal outcome — read it and adapt). "
                "Use market orders for immediate execution; use limit/stop for "
                "resting orders. Optionally attach take_profit / stop_loss "
                "bracket legs. DEMO MONEY ONLY — never real funds."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "type": {
                        "type": "string",
                        "enum": ["market", "limit", "stop", "stop_limit"],
                        "default": "market",
                    },
                    "quantity": {"type": "number", "description": "Units (> 0)."},
                    "limit_price": {"type": "number"},
                    "stop_price": {"type": "number"},
                    "take_profit": {"type": "number"},
                    "stop_loss": {"type": "number"},
                    "time_in_force": {
                        "type": "string",
                        "enum": ["gtc", "day", "ioc"],
                        "default": "gtc",
                    },
                },
                "required": ["symbol", "side", "quantity"],
            },
        },
        {
            "name": "cancel_order",
            "description": "Cancel a working order by its id.",
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
        {
            "name": "modify_order",
            "description": (
                "Modify a working order's quantity, prices, or bracket legs. "
                "Only include the fields you want to change."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "quantity": {"type": "number"},
                    "limit_price": {"type": "number"},
                    "stop_price": {"type": "number"},
                    "take_profit": {"type": "number"},
                    "stop_loss": {"type": "number"},
                },
                "required": ["order_id"],
            },
        },
        {
            "name": "run_backtest",
            "description": (
                "Backtest a Strategy SDK source file over historical data and "
                "return P&L-first performance metrics. Use to validate an idea "
                "before trading it live. Provide 'code' (Python strategy source) "
                "OR 'strategy_id' referencing a saved strategy."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Strategy SDK source code."},
                    "strategy_id": {"type": "string"},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "interval": {"type": "string", "default": "1d"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "starting_balance": {"type": "number", "default": 100000.0},
                    "leverage": {"type": "number", "default": 1.0},
                    "commission_bps": {"type": "number", "default": 0.0},
                    "slippage_bps": {"type": "number", "default": 0.0},
                    "params": {"type": "object"},
                },
                "required": ["symbols"],
            },
        },
        {
            "name": "get_market_summary",
            "description": (
                "Get a broad market snapshot: top movers up/down plus a handful "
                "of representative crypto and stock quotes. Use for orientation."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


# The canonical set of tool names, for validation / MCP registration.
TOOL_NAMES: tuple[str, ...] = tuple(spec["name"] for spec in tool_specs())


# --------------------------------------------------------------------------- #
# Dispatch                                                                    #
# --------------------------------------------------------------------------- #
def _dump(obj: Any) -> Any:
    """Best-effort JSON-serialize a Pydantic model, list, or plain value."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_dump(o) for o in obj]
    return obj


async def dispatch(
    name: str,
    args: dict,
    *,
    engine,
    market_data,
    backtester,
    portfolio_id: str,
) -> dict:
    """Execute one tool call and return a JSON-serializable result dict.

    Parameters
    ----------
    name:
        The tool name (must be one of ``TOOL_NAMES``).
    args:
        The tool arguments as provided by the model (already parsed from JSON).
    engine / market_data / backtester:
        The live singletons the tool operates on. All order placement is routed
        through ``engine.place_order`` — the shared risk-check path.
    portfolio_id:
        The portfolio these tools act on (bound per session/chat, not chosen by
        the model, so the agent can never touch another account).

    Errors are captured and returned as ``{"error": "..."}`` so the agent loop
    can continue and react rather than crashing mid-conversation.
    """
    args = args or {}
    try:
        # ---- Market data (read-only) ------------------------------------- #
        if name == "get_quote":
            q = await market_data.get_quote(args["symbol"])
            return _dump(q)

        if name == "get_ohlcv":
            resp = await market_data.get_ohlcv(
                args["symbol"],
                args.get("interval", "1d"),
                start=args.get("start"),
                end=args.get("end"),
                limit=int(args.get("limit", 100)),
            )
            return _dump(resp)

        if name == "search_symbols":
            results = await market_data.search_symbols(
                args["query"], int(args.get("limit", 25))
            )
            return {"results": _dump(results)}

        if name == "get_market_summary":
            return await _market_summary(market_data)

        # ---- Portfolio / account (read-only) ----------------------------- #
        if name == "get_portfolio":
            p = await engine.get_portfolio(portfolio_id)
            return _dump(p)

        if name == "get_positions":
            positions = await engine.get_positions(portfolio_id)
            return {"positions": _dump(positions)}

        if name == "get_open_orders":
            orders = await engine.get_open_orders(portfolio_id)
            return {"open_orders": _dump(orders)}

        if name == "get_trade_history":
            fills = await engine.get_trade_history(
                portfolio_id, limit=int(args.get("limit", 50))
            )
            return {"fills": _dump(fills)}

        # ---- Orders (shared risk-check path) ----------------------------- #
        if name == "place_order":
            req = OrderRequest(
                symbol=args["symbol"],
                side=OrderSide(args["side"]),
                type=OrderType(args.get("type", "market")),
                quantity=float(args["quantity"]),
                limit_price=_opt_float(args.get("limit_price")),
                stop_price=_opt_float(args.get("stop_price")),
                take_profit=_opt_float(args.get("take_profit")),
                stop_loss=_opt_float(args.get("stop_loss")),
                time_in_force=TimeInForce(args.get("time_in_force", "gtc")),
                source="ai",
            )
            order = await engine.place_order(portfolio_id, req)
            return _dump(order)

        if name == "cancel_order":
            order = await engine.cancel_order(portfolio_id, args["order_id"])
            return _dump(order)

        if name == "modify_order":
            order = await engine.modify_order(
                portfolio_id,
                args["order_id"],
                quantity=_opt_float(args.get("quantity")),
                limit_price=_opt_float(args.get("limit_price")),
                stop_price=_opt_float(args.get("stop_price")),
                take_profit=_opt_float(args.get("take_profit")),
                stop_loss=_opt_float(args.get("stop_loss")),
            )
            return _dump(order)

        # ---- Backtest ---------------------------------------------------- #
        if name == "run_backtest":
            req = BacktestRequest(
                code=args.get("code"),
                strategy_id=args.get("strategy_id"),
                symbols=list(args.get("symbols", [])),
                interval=args.get("interval", "1d"),
                start=args.get("start"),
                end=args.get("end"),
                starting_balance=float(args.get("starting_balance", 100_000.0)),
                leverage=float(args.get("leverage", 1.0)),
                commission_bps=float(args.get("commission_bps", 0.0)),
                slippage_bps=float(args.get("slippage_bps", 0.0)),
                params=args.get("params") or {},
            )
            result = await backtester.run(req)
            return _dump(result)

        return {"error": f"unknown tool: {name}"}

    except DataUnavailableError as exc:
        # Never fabricate — surface the honest unavailable state.
        return {"error": f"data unavailable: {exc}", "data_unavailable": True}
    except QuantDeckError as exc:
        return {"error": str(exc)}
    except (KeyError, ValueError, TypeError) as exc:
        # Bad/missing args from the model — report so it can retry correctly.
        return {"error": f"invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - keep the agent loop alive
        return {"error": f"tool '{name}' failed: {exc}"}


def _opt_float(value: Any) -> float | None:
    """Coerce an optional numeric arg to float, preserving None."""
    if value is None:
        return None
    return float(value)


async def _market_summary(market_data) -> dict:
    """Build a lightweight market snapshot from a few representative symbols.

    We deliberately keep this small and resilient: individual symbol failures
    are skipped rather than fabricated, and the movers lists are derived from
    whatever quotes we could actually fetch.
    """
    stock_syms = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA", "META", "SPY"]
    crypto_syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

    stocks: list[dict] = []
    crypto: list[dict] = []
    for sym in stock_syms:
        try:
            stocks.append(_dump(await market_data.get_quote(sym)))
        except QuantDeckError:
            continue
    for sym in crypto_syms:
        try:
            crypto.append(_dump(await market_data.get_quote(sym)))
        except QuantDeckError:
            continue

    # Rank all fetched quotes by percent change for movers, ignoring unknowns.
    ranked = [q for q in (stocks + crypto) if q.get("change_percent") is not None]
    ranked.sort(key=lambda q: q["change_percent"], reverse=True)
    movers_up = ranked[:5]
    movers_down = list(reversed(ranked[-5:])) if len(ranked) > 5 else []

    return {
        "movers_up": movers_up,
        "movers_down": movers_down,
        "crypto": crypto,
        "stocks": stocks,
    }

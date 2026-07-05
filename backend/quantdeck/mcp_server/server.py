"""QuantDeck MCP (Model Context Protocol) stdio server.

This server exposes the SAME trading toolbox the in-process AI agent uses
(``quantdeck.ai.tools.tool_specs``) to any MCP client. Rather than importing the
engine directly, every tool call is proxied over HTTP to a RUNNING QuantDeck
REST API. That keeps a single source of truth: the running service enforces the
shared risk-check path, so an MCP client can never bypass it.

Configuration (environment variables):
  * ``QUANTDECK_API_URL``       Base URL of the running API. Default
                                ``http://localhost:8000``.
  * ``QUANTDECK_PORTFOLIO_ID``  Default portfolio id used when a tool call omits
                                one. Can be overridden per call via a
                                ``portfolio_id`` argument.

Requirements: the ``mcp`` SDK (``pip install mcp``) and ``httpx`` (already a
QuantDeck dependency). Imports of ``mcp`` are deferred into ``main()`` so this
module imports cleanly even where the SDK is not installed.

Ready-to-paste client config (e.g. Claude Desktop ``claude_desktop_config.json``)::

    {
      "mcpServers": {
        "quantdeck": {
          "command": "python",
          "args": ["-m", "quantdeck.mcp_server.server"],
          "env": {
            "QUANTDECK_API_URL": "http://localhost:8000",
            "QUANTDECK_PORTFOLIO_ID": "<your-portfolio-id>"
          }
        }
      }
    }

DEMO MONEY ONLY — no real broker, no real funds, ever.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..ai import tools as ai_tools

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #
API_URL = os.getenv("QUANTDECK_API_URL", "http://localhost:8000").rstrip("/")
DEFAULT_PORTFOLIO_ID = os.getenv("QUANTDECK_PORTFOLIO_ID", "")

SERVER_NAME = "quantdeck"


# --------------------------------------------------------------------------- #
# REST proxy                                                                  #
# --------------------------------------------------------------------------- #
async def _call_api(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> Any:
    """Call the QuantDeck REST API and return parsed JSON (or an error dict)."""
    url = f"{API_URL}{path}"
    try:
        resp = await client.request(method, url, timeout=60.0, **kwargs)
    except httpx.HTTPError as exc:
        return {"error": f"cannot reach QuantDeck API at {url}: {exc}"}
    if resp.status_code >= 400:
        # Surface the API's own error body when present.
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        return {"error": f"API {resp.status_code}", "detail": body}
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"error": "non-JSON response", "detail": resp.text}


def _portfolio_id(args: dict) -> str:
    """Resolve the target portfolio id from args or the environment default."""
    pid = str(args.get("portfolio_id") or DEFAULT_PORTFOLIO_ID or "").strip()
    return pid


async def _dispatch_over_rest(
    client: httpx.AsyncClient, name: str, args: dict
) -> Any:
    """Map a tool call to the corresponding REST endpoint and execute it.

    Mirrors ``quantdeck.ai.tools.dispatch`` but over HTTP against the running
    API, so orders still traverse the service's shared risk-check path.
    """
    args = args or {}

    # ---- Market data (no portfolio needed) ------------------------------- #
    if name == "get_quote":
        # Fully encode the symbol so crypto pairs ("BTC/USDT") stay one path
        # segment ("/api/quote/BTC%2FUSDT") per the API contract.
        sym = quote(str(args["symbol"]), safe="")
        return await _call_api(client, "GET", f"/api/quote/{sym}")

    if name == "get_ohlcv":
        params = {"interval": args.get("interval", "1d"), "limit": args.get("limit", 100)}
        if args.get("start"):
            params["start"] = args["start"]
        if args.get("end"):
            params["end"] = args["end"]
        sym = quote(str(args["symbol"]), safe="")
        return await _call_api(client, "GET", f"/api/ohlcv/{sym}", params=params)

    if name == "search_symbols":
        return await _call_api(
            client,
            "GET",
            "/api/search",
            params={"q": args["query"], "limit": args.get("limit", 25)},
        )

    if name == "get_market_summary":
        return await _call_api(client, "GET", "/api/market/summary")

    # ---- Everything below needs a portfolio id --------------------------- #
    pid = _portfolio_id(args)
    if not pid:
        return {
            "error": (
                "no portfolio id — pass 'portfolio_id' or set "
                "QUANTDECK_PORTFOLIO_ID."
            )
        }

    if name == "get_portfolio":
        return await _call_api(client, "GET", f"/api/portfolios/{pid}")

    if name == "get_positions":
        return await _call_api(client, "GET", f"/api/portfolios/{pid}/positions")

    if name == "get_open_orders":
        return await _call_api(
            client, "GET", f"/api/portfolios/{pid}/orders", params={"open": "true"}
        )

    if name == "get_trade_history":
        return await _call_api(
            client,
            "GET",
            f"/api/portfolios/{pid}/fills",
            params={"limit": args.get("limit", 50)},
        )

    if name == "place_order":
        body = {
            "symbol": args["symbol"],
            "side": args["side"],
            "type": args.get("type", "market"),
            "quantity": args["quantity"],
            "limit_price": args.get("limit_price"),
            "stop_price": args.get("stop_price"),
            "take_profit": args.get("take_profit"),
            "stop_loss": args.get("stop_loss"),
            "time_in_force": args.get("time_in_force", "gtc"),
            "source": "mcp",
        }
        return await _call_api(
            client, "POST", f"/api/portfolios/{pid}/orders", json=body
        )

    if name == "cancel_order":
        return await _call_api(
            client,
            "POST",
            f"/api/portfolios/{pid}/orders/{args['order_id']}/cancel",
        )

    if name == "modify_order":
        body = {
            k: args[k]
            for k in ("quantity", "limit_price", "stop_price", "take_profit", "stop_loss")
            if args.get(k) is not None
        }
        return await _call_api(
            client,
            "POST",
            f"/api/portfolios/{pid}/orders/{args['order_id']}/modify",
            json=body,
        )

    if name == "run_backtest":
        body = {
            "code": args.get("code"),
            "strategy_id": args.get("strategy_id"),
            "symbols": args.get("symbols", []),
            "interval": args.get("interval", "1d"),
            "start": args.get("start"),
            "end": args.get("end"),
            "starting_balance": args.get("starting_balance", 100_000.0),
            "leverage": args.get("leverage", 1.0),
            "commission_bps": args.get("commission_bps", 0.0),
            "slippage_bps": args.get("slippage_bps", 0.0),
            "params": args.get("params") or {},
        }
        return await _call_api(client, "POST", "/api/backtest", json=body)

    return {"error": f"unknown tool: {name}"}


# --------------------------------------------------------------------------- #
# MCP server wiring                                                           #
# --------------------------------------------------------------------------- #
def _build_server(http_client: httpx.AsyncClient):
    """Create and configure the MCP Server with our tool handlers.

    ``mcp`` is imported here (not at module top) so this module imports cleanly
    in environments where the SDK is not installed (tests, py_compile).
    """
    from mcp.server import Server
    import mcp.types as mcp_types

    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        """Advertise the QuantDeck toolbox, reusing the AI tool specs verbatim.

        Each tool gains an optional ``portfolio_id`` property so an MCP client
        may target a specific portfolio without relying on the env default.
        """
        out: list[Any] = []
        for spec in ai_tools.tool_specs():
            schema = json.loads(json.dumps(spec["input_schema"]))  # deep copy
            props = schema.setdefault("properties", {})
            props.setdefault(
                "portfolio_id",
                {
                    "type": "string",
                    "description": (
                        "Target portfolio id (defaults to QUANTDECK_PORTFOLIO_ID)."
                    ),
                },
            )
            out.append(
                mcp_types.Tool(
                    name=spec["name"],
                    description=spec["description"],
                    inputSchema=schema,
                )
            )
        return out

    @server.call_tool()
    async def call_tool(name: str, arguments: Optional[dict]) -> list[Any]:
        """Execute a tool by proxying to the running QuantDeck REST API."""
        result = await _dispatch_over_rest(http_client, name, arguments or {})
        text = json.dumps(result, indent=2, default=str)
        return [mcp_types.TextContent(type="text", text=text)]

    return server


async def _run() -> None:
    """Async entrypoint: open the HTTP client and serve MCP over stdio."""
    from mcp.server.stdio import stdio_server

    async with httpx.AsyncClient() as http_client:
        server = _build_server(http_client)
        init_options = server.create_initialization_options()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console entrypoint — run the stdio MCP server."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()

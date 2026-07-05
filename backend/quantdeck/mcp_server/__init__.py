"""QuantDeck MCP server package.

A standalone Model Context Protocol (stdio) server that exposes QuantDeck's
trading tools to any MCP-capable client (Claude Desktop, IDEs, etc.). Every
tool call is proxied over HTTP to a RUNNING QuantDeck REST API, so the MCP
server never re-implements business logic — the API remains the single source
of truth (and the single risk-check path for orders).

Run it with:  ``python -m quantdeck.mcp_server.server``
"""

from .server import main  # noqa: F401  (re-export for convenience)

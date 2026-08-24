"""FastAPI application factory.

`create_app()` builds the ASGI app: it wires a lifespan that initializes the
database and constructs the shared singletons (market data, engine, strategy
manager, backtester, AI manager), configures CORS from settings, mounts every
REST router under `/api`, registers the two WebSocket endpoints, and exposes a
health probe.

The app is intentionally thin — all behavior lives in the engine / market-data
/ strategy / AI modules. This file is the integrator that stitches them into
one HTTP surface matching `docs/API_CONTRACT.md`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import settings
from ..db.database import init_db
from . import deps
from .deps import setup_singletons, teardown_singletons
from .routes_ai import router as ai_router
from .routes_backtest import router as backtest_router
from .routes_market import router as market_router
from .routes_order import router as order_router
from .routes_portfolio import router as portfolio_router
from .routes_strategy import router as strategy_router
from .ws import register_ws

log = logging.getLogger("quantdeck.api.app")

__version__ = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + wire singletons (starts market data, loads the
    engine from DB). Shutdown: release market-data resources."""
    await init_db()
    await setup_singletons()
    log.info("QuantDeck API started (version=%s)", __version__)
    try:
        yield
    finally:
        await teardown_singletons()
        log.info("QuantDeck API stopped")


def _provider_status() -> dict:
    """A small, honest summary of which data providers are configured.

    Keys are provider names; values indicate whether credentials are present
    (yahoo needs none, so it's always 'available')."""
    return {
        "yahoo": "available",            # public REST, no key
        "finnhub": "configured" if settings.finnhub_api_key else "unconfigured",
        "alpaca": (
            "configured"
            if (settings.alpaca_api_key and settings.alpaca_api_secret)
            else "unconfigured"
        ),
    }


def create_app() -> FastAPI:
    """Construct and return the configured FastAPI application."""
    app = FastAPI(
        title="QuantDeck API",
        version=__version__,
        summary="AI-driven paper-trading platform (demo money only).",
        lifespan=lifespan,
    )

    # CORS — the React frontend is served from a different origin in dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST routers — every path is prefixed with /api per the contract.
    app.include_router(market_router, prefix="/api", tags=["market"])
    app.include_router(portfolio_router, prefix="/api", tags=["portfolio"])
    app.include_router(order_router, prefix="/api", tags=["order"])
    app.include_router(strategy_router, prefix="/api", tags=["strategy"])
    app.include_router(backtest_router, prefix="/api", tags=["backtest"])
    app.include_router(ai_router, prefix="/api", tags=["ai"])

    # WebSockets: /ws/market and /ws/events.
    register_ws(app)

    @app.get("/api/health", tags=["health"])
    async def health() -> dict:
        """Liveness + capability probe for the UI and ops."""
        return {
            "status": "ok",
            "version": __version__,
            "ai_enabled": settings.ai_enabled,
            "providers": _provider_status(),
            # Convenience for the frontend so it can default a portfolio.
            "default_portfolio_id": deps.get_default_portfolio_id(),
        }

    # ------------------------------------------------------------------ #
    # Serve the built React frontend from the same origin (production).   #
    # In a deployed build the Vite output is copied next to the backend   #
    # (see the Dockerfile). Because the frontend uses relative URLs        #
    # (/api, ws://<host>/ws), one origin means zero CORS/config. When the  #
    # build isn't present (local dev, where Vite serves on :5173), we fall #
    # back to a small JSON note at "/".                                    #
    # ------------------------------------------------------------------ #
    _mount_frontend(app)

    return app


def _frontend_dir() -> Optional[Path]:
    """Locate the built frontend (index.html + assets), if present.

    Checks a few conventional spots so it works whether the build sits next to
    the package (Docker) or in the repo's frontend/dist (local `npm run build`).
    """
    candidates = [
        Path(__file__).resolve().parents[2] / "frontend_dist",   # Docker layout
        Path(__file__).resolve().parents[3] / "frontend" / "dist",  # repo layout
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def _mount_frontend(app: FastAPI) -> None:
    frontend = _frontend_dir()

    if frontend is None:
        @app.get("/", include_in_schema=False)
        async def root() -> JSONResponse:
            """Dev fallback — the SPA is served by Vite on :5173."""
            return JSONResponse(
                {
                    "name": "QuantDeck API",
                    "version": __version__,
                    "docs": "/docs",
                    "api_base": "/api",
                    "note": "Frontend build not found; run the Vite dev server "
                    "or build it. Paper-trading only — demo money.",
                }
            )
        return

    # Serve hashed static assets (JS/CSS) with normal file handling.
    assets = frontend / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    index_file = frontend / "index.html"

    # SPA catch-all: any non-/api, non-/ws path returns index.html so client-side
    # routes like /terminal/AAPL deep-link correctly. Real files (favicon,
    # etc.) at the frontend root are served directly.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "api", "ws/", "ws", "docs", "redoc", "openapi")):
            # Let the real routes 404 naturally rather than shadow them.
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = frontend / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_file))

    log.info("serving frontend from %s", frontend)

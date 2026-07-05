"""QuantDeck backend entrypoint.

Run the API with:

    python -m uvicorn quantdeck.api.app:create_app --factory --reload

or simply:

    python main.py

This module builds the ASGI app via the application factory and, when executed
directly, serves it with uvicorn bound to the host/port from settings.
"""
from __future__ import annotations

import uvicorn

from quantdeck.api.app import create_app
from quantdeck.core.config import settings

# Module-level app so ASGI servers can import `main:app` if preferred.
app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
    )

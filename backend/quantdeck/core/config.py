"""Central configuration, loaded from environment / .env.

Only READ-ONLY market-data credentials are ever accepted here. There is no
setting that enables real-money trading — by design.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_prefix="QUANTDECK_",
        extra="ignore",
    )

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # --- Database ---
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'quantdeck.db'}"

    # --- Market data (READ-ONLY keys only) ---
    finnhub_api_key: str = ""          # optional: real-time US stock trades
    alpaca_api_key: str = ""           # optional: real-time IEX feed
    alpaca_api_secret: str = ""
    alpha_vantage_api_key: str = ""    # optional: delayed fallback
    # Preferred US equity provider order (first that is configured/working wins).
    us_equity_providers: list[str] = Field(
        default_factory=lambda: ["finnhub", "alpaca", "yahoo"]
    )
    crypto_provider: str = "binance"

    # Cache TTLs (seconds)
    quote_cache_ttl: float = 2.0
    ohlcv_cache_ttl: float = 30.0
    symbol_cache_ttl: float = 86_400.0

    # --- AI (for the autonomous/manual trading loop) ---
    # Uses the Anthropic API if a key is present; the AI features degrade
    # gracefully to disabled if not configured.
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-5"
    ai_max_steps: int = 40             # hard cap on tool-use iterations per run

    # --- Strategy sandbox ---
    strategy_timeout_seconds: float = 30.0     # per-callback wall clock
    strategy_memory_mb: int = 512
    backtest_max_bars: int = 200_000

    # --- Defaults ---
    default_starting_balance: float = 100_000.0
    default_leverage: float = 1.0
    default_fill_mode: str = "realistic"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

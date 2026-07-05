"""Strategy runtime package.

User strategies subclass `Strategy` (from `.sdk`) and implement lifecycle hooks.
The same strategy file runs unchanged in live-paper mode (via `.runner`) and in
backtest mode (via `quantdeck.backtest`), because both drive it through the same
`StrategyContext` surface.
"""
from .sdk import Strategy, StrategyContext  # noqa: F401

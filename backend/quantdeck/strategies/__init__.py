"""Strategy runtime package.

User strategies subclass `Strategy` (from `.sdk`) and implement lifecycle hooks.
Strategies run in live-paper mode (via `.runner`), driven through the
`StrategyContext` surface.
"""
from .sdk import Strategy, StrategyContext  # noqa: F401

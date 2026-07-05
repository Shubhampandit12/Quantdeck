"""Simulation engine package. The single source of truth for orders, fills,
positions, and P&L. Import `TradingEngine` from `.engine`."""
from __future__ import annotations

from .engine import TradingEngine

__all__ = ["TradingEngine"]

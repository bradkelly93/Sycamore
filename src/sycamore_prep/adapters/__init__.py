"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import (
    FundamentalsProvider,
    PriceProvider,
    EventProbabilityProvider,
    FinancialsFrame,
    MARKET_COLUMNS,
)
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider
from .polymarket import PolymarketProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "EventProbabilityProvider",
    "FinancialsFrame",
    "MARKET_COLUMNS",
    "EdgarProvider",
    "YFinanceProvider",
    "PolymarketProvider",
]

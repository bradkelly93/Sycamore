"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import (
    FundamentalsProvider,
    PriceProvider,
    VolatilityProvider,
    FinancialsFrame,
    VolatilityFrame,
)
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider
from .tastytrade_provider import TastytradeProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "VolatilityProvider",
    "FinancialsFrame",
    "VolatilityFrame",
    "EdgarProvider",
    "YFinanceProvider",
    "TastytradeProvider",
]

"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import FundamentalsProvider, PriceProvider, FinancialsFrame
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "FinancialsFrame",
    "EdgarProvider",
    "YFinanceProvider",
]

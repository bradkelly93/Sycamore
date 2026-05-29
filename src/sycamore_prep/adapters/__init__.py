"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import FundamentalsProvider, PriceProvider, FilingsProvider, FinancialsFrame
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "FilingsProvider",
    "FinancialsFrame",
    "EdgarProvider",
    "YFinanceProvider",
]

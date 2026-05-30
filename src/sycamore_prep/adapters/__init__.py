"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import (
    FundamentalsProvider,
    PriceProvider,
    TechnicalScreenProvider,
    FinancialsFrame,
)
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider
from .tradingview import TradingViewProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "TechnicalScreenProvider",
    "FinancialsFrame",
    "EdgarProvider",
    "YFinanceProvider",
    "TradingViewProvider",
]

"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import (
    FundamentalsProvider,
    PriceProvider,
    FilingsProvider,
    VolatilityProvider,
    TechnicalScreenProvider,
    FinancialsFrame,
    VolatilityFrame,
)
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider
from .tastytrade_provider import TastytradeProvider
from .tradingview import TradingViewProvider
from .csv_screen import CsvScreenProvider
from .trend_regime import TrendRegimeProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "FilingsProvider",
    "VolatilityProvider",
    "TechnicalScreenProvider",
    "FinancialsFrame",
    "VolatilityFrame",
    "EdgarProvider",
    "YFinanceProvider",
    "TastytradeProvider",
    "TradingViewProvider",
    "CsvScreenProvider",
    "TrendRegimeProvider",
]

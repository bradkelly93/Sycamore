"""Data adapter layer. Downstream code must depend only on `base` interfaces."""

from .base import (
    FundamentalsProvider,
    PriceProvider,
    FilingsProvider,
    VolatilityProvider,
    TechnicalScreenProvider,
    EventProbabilityProvider,
    FinancialsFrame,
    VolatilityFrame,
    MARKET_COLUMNS,
)
from .edgar import EdgarProvider
from .yfinance_provider import YFinanceProvider
from .tastytrade_provider import TastytradeProvider
from .tradingview import TradingViewProvider
from .csv_screen import CsvScreenProvider
from .trend_regime import TrendRegimeProvider
from .polymarket import PolymarketProvider

__all__ = [
    "FundamentalsProvider",
    "PriceProvider",
    "FilingsProvider",
    "VolatilityProvider",
    "TechnicalScreenProvider",
    "EventProbabilityProvider",
    "FinancialsFrame",
    "VolatilityFrame",
    "MARKET_COLUMNS",
    "EdgarProvider",
    "YFinanceProvider",
    "TastytradeProvider",
    "TradingViewProvider",
    "CsvScreenProvider",
    "TrendRegimeProvider",
    "PolymarketProvider",
]

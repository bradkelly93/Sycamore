"""yfinance adapter. NON-PRIMARY — every row carries `source` tag.

Used for prices, market cap, and share counts where SEC tagging is sparse or
slow. Wrap calls in retry; cache aggressively. Downstream code must keep the
`source` column when joining these fields to EDGAR data.
"""

from __future__ import annotations

import time
from typing import Iterable

import pandas as pd

from . import cache
from .base import CompanyMeta, FinancialsFrame, PriceProvider


SOURCE_TAG = "yfinance (non-primary)"


def _yf():
    # Import lazily so unit tests that don't touch yfinance don't need network.
    import yfinance as yf
    return yf


class YFinanceProvider(PriceProvider):
    name = "yfinance"

    def __init__(self, max_retries: int = 3):
        self._max_retries = max_retries

    def _retry(self, fn, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # yfinance is flaky; broad except is intentional
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"yfinance call failed after {self._max_retries} retries") from last

    def get_prices(self, ticker: str, start: str | None = None) -> pd.DataFrame:
        cached = cache.load_prices(ticker)
        if cached is not None and start is None:
            return cached
        yf = _yf()
        hist = self._retry(lambda: yf.Ticker(ticker).history(period="max", auto_adjust=False))
        if hist.empty:
            return hist
        hist = hist.reset_index().rename(columns=str.lower)
        hist["ticker"] = ticker.upper()
        hist["source"] = SOURCE_TAG
        cache.save_prices(ticker, hist)
        if start:
            hist = hist[hist["date"] >= pd.Timestamp(start)]
        return hist

    def get_market_cap(self, ticker: str) -> float | None:
        yf = _yf()
        info = self._retry(lambda: yf.Ticker(ticker).get_info())
        mc = info.get("marketCap") if isinstance(info, dict) else None
        return float(mc) if mc else None

    def get_shares(self, ticker: str) -> float | None:
        yf = _yf()
        info = self._retry(lambda: yf.Ticker(ticker).get_info())
        s = info.get("sharesOutstanding") if isinstance(info, dict) else None
        return float(s) if s else None

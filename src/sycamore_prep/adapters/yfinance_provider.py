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


def _yf_symbol(ticker: str) -> str:
    """yfinance uses a dash for share classes (BRK.B -> BRK-B, MOG.A -> MOG-A),
    while SEC/EDGAR and our config use the dot form. Translate only at the
    yfinance boundary; cache keys and the `ticker` column keep the dot form.
    """
    return ticker.upper().replace(".", "-")


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
        hist = self._retry(lambda: yf.Ticker(_yf_symbol(ticker)).history(period="max", auto_adjust=False))
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
        """Try fast_info first (cheap, reliable); fall back to slow get_info."""
        yf = _yf()
        t = yf.Ticker(_yf_symbol(ticker))
        try:
            fi = t.fast_info
            mc = fi.get("market_cap") or fi.get("marketCap")
            if mc:
                return float(mc)
        except Exception:  # noqa: BLE001
            pass
        try:
            info = self._retry(lambda: t.get_info())
            mc = info.get("marketCap") if isinstance(info, dict) else None
            return float(mc) if mc else None
        except Exception:  # noqa: BLE001
            return None

    def get_current_price(self, ticker: str) -> float | None:
        """Latest closing price. Used by the screener to compute market cap as
        price × EDGAR shares when get_market_cap fails — keeps the share count
        primary-source per CLAUDE.md."""
        yf = _yf()
        try:
            fi = yf.Ticker(_yf_symbol(ticker)).fast_info
            price = fi.get("last_price") or fi.get("lastPrice") or fi.get("previousClose")
            if price:
                return float(price)
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_shares(self, ticker: str) -> float | None:
        yf = _yf()
        try:
            fi = yf.Ticker(_yf_symbol(ticker)).fast_info
            s = fi.get("shares") or fi.get("sharesOutstanding")
            if s:
                return float(s)
        except Exception:  # noqa: BLE001
            pass
        try:
            info = self._retry(lambda: yf.Ticker(_yf_symbol(ticker)).get_info())
            s = info.get("sharesOutstanding") if isinstance(info, dict) else None
            return float(s) if s else None
        except Exception:  # noqa: BLE001
            return None

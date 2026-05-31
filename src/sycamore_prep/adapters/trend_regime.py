"""Trend-regime overlay provider. NON-PRIMARY context only.

Computes a transparent Bull/Neutral/Bear trend regime for each candidate ticker
from the daily price history we already pull (yfinance), via metrics.trend. No
TradingView account or dependency, fully offline-replayable from the price
cache. The screener overlays it exactly like the other TechnicalScreenProvider
implementations — and it never enters the three-attribute score.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from .base import TechnicalScreenProvider
from .yfinance_provider import YFinanceProvider
from ..config import load_config
from ..metrics.trend import trend_regime


SOURCE_TAG = "trend-proxy (non-primary, technical)"
_DEFAULT_PASS = {"Bull"}


class TrendRegimeProvider(TechnicalScreenProvider):
    name = "trend-proxy"

    def __init__(self, price_provider=None):
        self._prices = price_provider or YFinanceProvider()

    def get_screen(
        self, tickers: Iterable[str] | None = None, refresh: bool = False  # noqa: ARG002 — prices are cached by the price provider
    ) -> pd.DataFrame:
        cfg = load_config().tradingview
        pass_set = {str(v).strip() for v in cfg.pass_values} or _DEFAULT_PASS

        cols = ["ticker", "regime", "pct_above_200", "slope200_pct",
                "asof", "passes_screen", "source"]
        rows = []
        for t in (tickers or []):
            try:
                px = self._prices.get_prices(str(t))
            except Exception:  # noqa: BLE001 — price feed is non-primary/flaky
                continue
            if px is None or getattr(px, "empty", True) or "close" not in px.columns:
                continue
            info = trend_regime(px)
            rows.append({
                "ticker": str(t).upper(),
                "regime": info["regime"],
                "pct_above_200": info["pct_above_200"],
                "slope200_pct": info["slope200_pct"],
                "asof": info["asof"],
            })

        if not rows:
            return pd.DataFrame(columns=cols)
        out = pd.DataFrame(rows)
        out["passes_screen"] = out["regime"].isin(pass_set)
        out["source"] = SOURCE_TAG
        return out

"""Trend-regime proxy tests — fully offline, deterministic price series."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.adapters.trend_regime import SOURCE_TAG, TrendRegimeProvider
from sycamore_prep.metrics.trend import _classify, trend_regime


def _series(values):
    return pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=len(values), freq="D"),
        "close": values,
    })


def test_classify_vote_table():
    assert _classify(True, True, True) == "Bull"
    assert _classify(False, False, False) == "Bear"
    assert _classify(True, False, False) == "Neutral"
    assert _classify(True, True, False) == "Neutral"


def test_rising_series_is_bull():
    info = trend_regime(_series([float(i) for i in range(1, 261)]))
    assert info["regime"] == "Bull"
    assert info["pct_above_200"] > 0
    assert info["slope200_pct"] > 0


def test_falling_series_is_bear():
    info = trend_regime(_series([float(i) for i in range(260, 0, -1)]))
    assert info["regime"] == "Bear"
    assert info["pct_above_200"] < 0


def test_short_series_is_insufficient_history():
    info = trend_regime(_series([100.0] * 50))
    assert info["regime"] == "insufficient_history"
    assert info["pct_above_200"] is None


class _FakePrices:
    def __init__(self, mapping):
        self._m = mapping

    def get_prices(self, ticker, start=None):  # noqa: ARG002
        return self._m.get(str(ticker).upper(), pd.DataFrame())


def test_provider_builds_tidy_frame():
    rising = _series([float(i) for i in range(1, 261)])
    falling = _series([float(i) for i in range(260, 0, -1)])
    prov = TrendRegimeProvider(price_provider=_FakePrices({"AAA": rising, "BBB": falling}))

    out = prov.get_screen(tickers=["AAA", "BBB", "CCC"]).set_index("ticker")
    assert out.loc["AAA", "regime"] == "Bull"
    assert out.loc["BBB", "regime"] == "Bear"
    assert "CCC" not in out.index                         # no prices -> skipped
    assert bool(out.loc["AAA", "passes_screen"]) is True   # Bull passes by default
    assert bool(out.loc["BBB", "passes_screen"]) is False
    assert (out["source"] == SOURCE_TAG).all()

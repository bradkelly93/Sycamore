"""Phase 3 comps unit tests — historical band alignment, cheapness percentile
direction, normalized earnings, and share-basis fallback. Offline-first: all
fixtures are hand-built, no network. (DCF math lives in test_reverse_dcf.py.)"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from sycamore_prep.adapters.base import FinancialsFrame
from sycamore_prep.comps.history import (
    align_prices_to_fy_ends,
    shares_by_period,
)
from sycamore_prep.comps.normalized import normalized_earnings
from sycamore_prep.metrics.valuation import cheapness_percentile, pe_history


def _ff(rows: list[dict]) -> FinancialsFrame:
    base = {"ticker": "TEST", "fy": None, "fp": "FY", "form": "10-K",
            "unit": "USD", "source": "edgar (primary)"}
    return FinancialsFrame(pd.DataFrame([{**base, **r} for r in rows]))


def _prices(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({"date": [r[0] for r in rows], "close": [r[1] for r in rows]})


# --------------------------------------------------------------------------- #
# Price → FY-end alignment
# --------------------------------------------------------------------------- #

def test_align_prices_picks_last_trading_day_before_weekend_fy_end():
    # 2022-12-31 is a Saturday → should resolve to Friday 2022-12-30's close.
    prices = _prices([
        ("2022-12-29", 100.0), ("2022-12-30", 101.0),
        ("2023-06-29", 200.0), ("2023-06-30", 201.0),
    ])
    out = align_prices_to_fy_ends(prices, ["2018-12-31", "2022-12-31", "2023-06-30"])
    # 2018 is before price history → dropped (no lookahead, no fabrication).
    assert list(out.index) == ["2022-12-31", "2023-06-30"]
    assert out["2022-12-31"] == 101.0   # Friday close, not the weekend
    assert out["2023-06-30"] == 201.0   # exact trading-day match


def test_align_prices_respects_backward_tolerance():
    prices = _prices([("2023-01-03", 50.0)])
    # Target 8 trading-days+ before the only price, beyond 7-day tolerance.
    out = align_prices_to_fy_ends(prices, ["2022-12-20"], tolerance_days=7)
    assert out.empty


# --------------------------------------------------------------------------- #
# P/E band
# --------------------------------------------------------------------------- #

def test_pe_history_band_drops_negative_eps_years():
    ff = _ff([
        {"concept": "EpsDiluted", "period": "2021-12-31", "value": 5.0, "unit": "USD/shares"},
        {"concept": "EpsDiluted", "period": "2022-12-31", "value": 6.0, "unit": "USD/shares"},
        {"concept": "EpsDiluted", "period": "2023-12-31", "value": -1.0, "unit": "USD/shares"},
    ])
    price = pd.Series({"2021-12-31": 50.0, "2022-12-31": 90.0, "2023-12-31": 30.0})
    band = pe_history(ff, price)
    assert band["2021-12-31"] == pytest.approx(10.0)   # 50 / 5
    assert band["2022-12-31"] == pytest.approx(15.0)   # 90 / 6
    assert math.isnan(band["2023-12-31"])              # negative EPS → no P/E


# --------------------------------------------------------------------------- #
# Cheapness percentile sign convention
# --------------------------------------------------------------------------- #

def test_cheapness_percentile_direction_for_lower_and_higher_is_cheap():
    pe_hist = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])
    # Today's P/E of 11 is near the cheap end → high cheapness percentile.
    assert cheapness_percentile(pe_hist, 11.0, lower_is_cheap=True) == pytest.approx(80.0)
    fcf_hist = pd.Series([0.02, 0.04, 0.06, 0.08, 0.10])
    # Today's FCF yield of 9% is near the rich-yield (cheap) end → high too.
    assert cheapness_percentile(fcf_hist, 0.09, lower_is_cheap=False) == pytest.approx(80.0)


def test_cheapness_percentile_needs_three_observations():
    assert math.isnan(cheapness_percentile(pd.Series([10.0, 12.0]), 11.0, lower_is_cheap=True))


# --------------------------------------------------------------------------- #
# Normalized earnings (5y mean margin × current revenue) + trough
# --------------------------------------------------------------------------- #

def test_normalized_earnings_five_year_mean_and_trough():
    years = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    rev = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0]
    oi = [100.0, 165.0, 180.0, 260.0, 280.0]   # margins 0.10, 0.15, 0.15, 0.20, 0.20
    rows = []
    for p, r, o in zip(years, rev, oi):
        rows.append({"concept": "Revenues", "period": p, "value": r})
        rows.append({"concept": "OperatingIncomeLoss", "period": p, "value": o})
    rows.append({"concept": "WeightedAverageSharesDiluted", "period": "2025-12-31",
                 "value": 100.0, "unit": "shares"})
    rows.append({"concept": "EpsDiluted", "period": "2025-12-31", "value": 2.50,
                 "unit": "USD/shares"})
    ff = _ff(rows)

    ne = normalized_earnings(ff, current_price=35.0, window=5, tax_rate=0.21)
    assert ne is not None
    assert ne.n_used == 5
    assert ne.mean_op_margin == pytest.approx(0.16)
    assert ne.trough_op_margin == pytest.approx(0.10)
    assert ne.current_revenue == pytest.approx(1400.0)
    assert ne.normalized_op_income == pytest.approx(224.0)          # 0.16 * 1400
    assert ne.normalized_net_income == pytest.approx(176.96)        # * 0.79
    assert ne.normalized_eps == pytest.approx(1.7696)              # / 100
    assert ne.normalized_pe == pytest.approx(35.0 / 1.7696)        # ~19.78
    assert ne.trough_pe == pytest.approx(35.0 / 1.106)            # ~31.65 (downside)
    assert ne.trailing_pe == pytest.approx(14.0)                   # 35 / 2.50


def test_normalized_earnings_uses_available_window_when_short():
    years = ["2023-12-31", "2024-12-31", "2025-12-31"]
    rows = []
    for p, r, o in zip(years, [1000.0, 1000.0, 1000.0], [100.0, 200.0, 300.0]):
        rows.append({"concept": "Revenues", "period": p, "value": r})
        rows.append({"concept": "OperatingIncomeLoss", "period": p, "value": o})
    rows.append({"concept": "WeightedAverageSharesDiluted", "period": "2025-12-31",
                 "value": 100.0, "unit": "shares"})
    ne = normalized_earnings(_ff(rows), current_price=20.0, window=5)
    assert ne.n_used == 3
    assert ne.mean_op_margin == pytest.approx(0.20)   # (0.10+0.20+0.30)/3


# --------------------------------------------------------------------------- #
# Share-basis fallback chain
# --------------------------------------------------------------------------- #

def test_shares_by_period_falls_back_to_implied_when_counts_absent():
    ff = _ff([
        {"concept": "NetIncomeLoss", "period": "2024-12-31", "value": 200.0},
        {"concept": "EpsDiluted", "period": "2024-12-31", "value": 2.0, "unit": "USD/shares"},
    ])
    s, basis = shares_by_period(ff, basis="wad")
    assert basis == "NetIncome/EpsDiluted (implied)"
    assert s["2024-12-31"] == pytest.approx(100.0)   # 200 / 2

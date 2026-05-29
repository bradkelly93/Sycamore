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


def test_normalized_earnings_subtracts_interest_before_tax():
    # Flat 20% op margin on $1,000 revenue, $40 interest, 100 shares.
    # norm op income = 200; pretax = 200 - 40 = 160; NI = 160 * 0.79 = 126.4.
    years = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    rows = []
    for p in years:
        rows.append({"concept": "Revenues", "period": p, "value": 1000.0})
        rows.append({"concept": "OperatingIncomeLoss", "period": p, "value": 200.0})
    rows.append({"concept": "InterestExpense", "period": "2025-12-31", "value": 40.0})
    rows.append({"concept": "WeightedAverageSharesDiluted", "period": "2025-12-31",
                 "value": 100.0, "unit": "shares"})
    ne = normalized_earnings(_ff(rows), current_price=20.0, window=5, tax_rate=0.21)
    assert ne.interest_expense == pytest.approx(40.0)
    assert ne.normalized_op_income == pytest.approx(200.0)
    assert ne.normalized_net_income == pytest.approx(126.4)   # (200 - 40) * 0.79
    assert ne.normalized_eps == pytest.approx(1.264)


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


# --------------------------------------------------------------------------- #
# End-to-end offline run_comps (subject + a price-less peer + a failing peer)
# --------------------------------------------------------------------------- #

from sycamore_prep.adapters import cache as cache_mod          # noqa: E402
from sycamore_prep.adapters import YFinanceProvider            # noqa: E402
from sycamore_prep.adapters.edgar import EdgarProvider         # noqa: E402
from sycamore_prep.comps import run_comps                      # noqa: E402

_M = 1_000_000
_YEARS = [2019, 2020, 2021, 2022, 2023, 2024]


def _ann(vals, unit="USD"):
    return {"units": {unit: [
        {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "fy": y, "fp": "FY",
         "form": "10-K", "filed": f"{y + 1}-02-15"}
        for y, v in zip(_YEARS, vals)
    ]}}


def _company_facts(scale=1.0):
    s = scale
    return {"facts": {"us-gaap": {
        "Revenues": _ann([v * _M * s for v in [1000, 1100, 1200, 1300, 1400, 1500]]),
        "OperatingIncomeLoss": _ann([v * _M * s for v in [150, 170, 180, 210, 240, 270]]),
        "NetIncomeLoss": _ann([v * _M * s for v in [100, 115, 120, 140, 160, 180]]),
        "EarningsPerShareDiluted": _ann([4.0, 4.6, 4.8, 5.6, 6.4, 7.2], unit="USD/shares"),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _ann([25 * _M] * 6, unit="shares"),
        "NetCashProvidedByUsedInOperatingActivities":
            _ann([v * _M * s for v in [180, 200, 210, 240, 270, 300]]),
        "PaymentsToAcquirePropertyPlantAndEquipment":
            _ann([v * _M * s for v in [40, 45, 45, 50, 55, 60]]),
        "DepreciationDepletionAndAmortization":
            _ann([v * _M * s for v in [50, 52, 54, 56, 58, 60]]),
        "LongTermDebt": _ann([400 * _M * s] * 6),
        "CashAndCashEquivalentsAtCarryingValue": _ann([60 * _M * s] * 6),
        "StockholdersEquity": _ann([v * _M * s for v in [800, 820, 860, 900, 960, 1020]]),
    }}}


def _fake_edgar_get(self, url: str):  # noqa: ARG001
    if "company_tickers.json" in url:
        return {"0": {"cik_str": 111, "ticker": "AAA", "title": "ALPHA INC"},
                "1": {"cik_str": 222, "ticker": "BBB", "title": "BETA CORP"}}
    if "CIK0000000111" in url:
        return _company_facts(1.0)
    if "CIK0000000222" in url:
        return _company_facts(0.8)
    raise AssertionError(f"unexpected url {url}")


def _price_df():
    dates = pd.bdate_range("2018-06-01", "2025-03-01")
    close = np.linspace(20.0, 65.0, len(dates))
    return pd.DataFrame({"date": dates, "close": close, "ticker": "X",
                         "source": "yfinance (non-primary)"})


@pytest.fixture
def patched(tmp_path, monkeypatch):
    monkeypatch.setattr(EdgarProvider, "_get", _fake_edgar_get)
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(YFinanceProvider, "get_market_cap",
                        lambda self, t: {"AAA": 1.5e9, "BBB": 0.9e9}.get(t))
    monkeypatch.setattr(YFinanceProvider, "get_current_price",
                        lambda self, t: {"AAA": 60.0, "BBB": 40.0}.get(t))
    # BBB has NO price history → its own-history bands must be NaN, not 0.
    monkeypatch.setattr(YFinanceProvider, "get_prices",
                        lambda self, t, start=None: pd.DataFrame() if t == "BBB" else _price_df())
    return tmp_path


def test_run_comps_end_to_end_offline(patched):
    out = patched / "comps_AAA.xlsx"
    res = run_comps("AAA", peers=["BBB", "CCC"], output_path=out)

    # Both artifacts written.
    assert out.exists()
    assert out.with_suffix(".md").exists()

    pt = res.peer_table
    # Subject first, peers, then aggregate rows — failing peer kept, not dropped.
    for tk in ["AAA", "BBB", "CCC", "PEER_MEDIAN", "PEER_MEAN"]:
        assert tk in pt.index

    # Sources tagged (CLAUDE.md #3).
    assert "edgar (primary)" in res.subject.sources
    assert "yfinance (non-primary)" in res.subject.sources

    # Subject: three bands with real percentiles (>=3 FYs of prices).
    bands = res.subject.history.bands
    assert set(bands) == {"PE", "EV_EBITDA", "FCF_Yield"}
    assert bands["PE"].n >= 3
    assert pd.notna(bands["PE"].percentile_cheap)

    # Reverse DCF present and ordered bear -> base -> bull (downside first).
    assert [c.label for c in res.subject.dcf_cases] == ["bear", "base", "bull"]
    assert res.subject.normalized is not None

    # Missing data renders NaN, never silent 0: BBB has no prices.
    assert pd.isna(pt.loc["BBB", "pe_pctile_own_hist"])
    assert pd.notna(pt.loc["BBB", "pe"])            # current multiple still computed
    # Failing peer CCC: visible error row, NaN metrics.
    assert pt.loc["CCC", "error"]
    assert pd.isna(pt.loc["CCC", "market_cap"])


def test_run_comps_no_prices_flag_skips_bands_but_keeps_comps(patched):
    res = run_comps("AAA", peers=["BBB"], output_path=patched / "comps_np.xlsx",
                    fetch_prices=False)
    # No prices → no own-history percentile, but current multiples + DCF still run.
    assert pd.isna(res.subject.own_history_pctile("PE"))
    assert pd.notna(res.subject.current.get("pe"))
    assert [c.label for c in res.subject.dcf_cases] == ["bear", "base", "bull"]

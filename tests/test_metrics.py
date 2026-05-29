"""Metric tests against hand-computed fixtures.

Strategy: build a minimal FinancialsFrame by hand with known values, then
verify each metric matches what we compute on paper.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from sycamore_prep.adapters.base import FinancialsFrame
from sycamore_prep.metrics import (
    ebitda_fy,
    fcf_margin_fy,
    fcf_yield_fy,
    free_cash_flow_fy,
    gross_margin_fy,
    interest_coverage_fy,
    is_bank,
    net_debt_fy,
    net_debt_to_ebitda_fy,
    nopat_fy,
    pe_fy,
    percentile_vs_history,
    revenue_fy,
    roe_fy,
    roic_fy,
    rotce_fy,
    tangible_book_value_fy,
)
from sycamore_prep.metrics.profitability import invested_capital_fy


def _ff(rows: list[dict]) -> FinancialsFrame:
    """Helper: build a FinancialsFrame from concept/period/value tuples."""
    base = {
        "ticker": "TEST", "fy": None, "fp": "FY", "form": "10-K",
        "unit": "USD", "source": "edgar (primary)",
    }
    expanded = [{**base, **r} for r in rows]
    return FinancialsFrame(pd.DataFrame(expanded))


# Two-year hand-computed industrial fixture --------------------------------- #
# FY 2022: Rev=1000, COGS=600, OpInc=200, NI=120, IntExp=20
#          LT Debt=400, ST Debt=100, Cash=50, Equity=800
#          CFO=180, CapEx=40
# FY 2023: Rev=1200, COGS=700, OpInc=260, NI=160, IntExp=20
#          LT Debt=400, ST Debt=100, Cash=80, Equity=900
#          CFO=220, CapEx=50

INDUSTRIAL_ROWS = [
    {"concept": "Revenues",                 "period": "2022-12-31", "value": 1000.0, "fy": 2022},
    {"concept": "Revenues",                 "period": "2023-12-31", "value": 1200.0, "fy": 2023},
    {"concept": "CostOfRevenue",            "period": "2022-12-31", "value": 600.0,  "fy": 2022},
    {"concept": "CostOfRevenue",            "period": "2023-12-31", "value": 700.0,  "fy": 2023},
    {"concept": "OperatingIncomeLoss",      "period": "2022-12-31", "value": 200.0,  "fy": 2022},
    {"concept": "OperatingIncomeLoss",      "period": "2023-12-31", "value": 260.0,  "fy": 2023},
    {"concept": "NetIncomeLoss",            "period": "2022-12-31", "value": 120.0,  "fy": 2022},
    {"concept": "NetIncomeLoss",            "period": "2023-12-31", "value": 160.0,  "fy": 2023},
    {"concept": "InterestExpense",          "period": "2022-12-31", "value": 20.0,   "fy": 2022},
    {"concept": "InterestExpense",          "period": "2023-12-31", "value": 20.0,   "fy": 2023},
    {"concept": "LongTermDebt",             "period": "2022-12-31", "value": 400.0,  "fy": 2022},
    {"concept": "LongTermDebt",             "period": "2023-12-31", "value": 400.0,  "fy": 2023},
    {"concept": "ShortTermDebt",            "period": "2022-12-31", "value": 100.0,  "fy": 2022},
    {"concept": "ShortTermDebt",            "period": "2023-12-31", "value": 100.0,  "fy": 2023},
    {"concept": "CashAndEquivalents",       "period": "2022-12-31", "value": 50.0,   "fy": 2022},
    {"concept": "CashAndEquivalents",       "period": "2023-12-31", "value": 80.0,   "fy": 2023},
    {"concept": "StockholdersEquity",       "period": "2022-12-31", "value": 800.0,  "fy": 2022},
    {"concept": "StockholdersEquity",       "period": "2023-12-31", "value": 900.0,  "fy": 2023},
    {"concept": "OperatingCashFlow",        "period": "2022-12-31", "value": 180.0,  "fy": 2022},
    {"concept": "OperatingCashFlow",        "period": "2023-12-31", "value": 220.0,  "fy": 2023},
    {"concept": "CapEx",                    "period": "2022-12-31", "value": 40.0,   "fy": 2022},
    {"concept": "CapEx",                    "period": "2023-12-31", "value": 50.0,   "fy": 2023},
]


def test_series_filter_excludes_multi_year_cumulative_entries():
    """3-year cumulative revenue rows from a 10-K comparative table have
    period_days ~1095. Without an upper bound, _last() picks them and any
    ratio mixing them with annual flows breaks. (CW: fcf_margin came back
    5.8% because Revenue was a 3-year cumulative ~$9.4B vs the real
    ~$3.3B annual.)"""
    from sycamore_prep.metrics.profitability import _series

    rows = [
        {"concept": "Revenues", "period": "2023-12-31", "value": 3000.0,
         "fy": 2023, "period_start": "2023-01-01", "period_days": 365},
        {"concept": "Revenues", "period": "2024-12-31", "value": 3300.0,
         "fy": 2024, "period_start": "2024-01-01", "period_days": 366},
        # 3-year cumulative for FY2024 — must be rejected.
        {"concept": "Revenues", "period": "2024-12-31", "value": 9400.0,
         "fy": 2024, "period_start": "2022-01-01", "period_days": 1095},
    ]
    ff = _ff(rows)
    rev = _series(ff, "Revenues")
    # Only the annual values survive; the cumulative is filtered out.
    assert list(rev.index) == ["2023-12-31", "2024-12-31"]
    assert list(rev.values) == [3000.0, 3300.0]


def test_series_filter_excludes_quarterly_entries_tagged_FY():
    """When `period_days` is present, FY queries must reject sub-annual spans.
    Reproduces the CW gross-margin >100% bug: standalone quarterly values
    tagged fp=FY were polluting the Revenue denominator."""
    from sycamore_prep.metrics.profitability import _series

    rows = [
        # Genuine FY entries (~365 days).
        {"concept": "Revenues", "period": "2023-12-31", "value": 3000.0,
         "fy": 2023, "period_start": "2023-01-01", "period_days": 365},
        {"concept": "Revenues", "period": "2024-12-31", "value": 3300.0,
         "fy": 2024, "period_start": "2024-01-01", "period_days": 366},
        # Standalone Q1 2024 mis-tagged FY (90-day span) — must be excluded.
        {"concept": "Revenues", "period": "2024-03-31", "value": 800.0,
         "fy": 2024, "period_start": "2024-01-01", "period_days": 90},
        # Instant balance-sheet entry (no start) — must be kept.
        {"concept": "Assets", "period": "2024-12-31", "value": 50000.0,
         "fy": 2024, "period_start": None, "period_days": None},
    ]
    ff = _ff(rows)
    rev = _series(ff, "Revenues")
    assert list(rev.index) == ["2023-12-31", "2024-12-31"]
    assert list(rev.values) == [3000.0, 3300.0]
    assets = _series(ff, "Assets")
    assert list(assets.values) == [50000.0]


def test_revenue_and_margins():
    ff = _ff(INDUSTRIAL_ROWS)
    assert revenue_fy(ff).iloc[-1] == 1200.0
    # GM 2023 = (1200-700)/1200 = 0.41667
    assert gross_margin_fy(ff).iloc[-1] == pytest.approx(500 / 1200)


def test_free_cash_flow_and_margin_and_yield():
    ff = _ff(INDUSTRIAL_ROWS)
    assert free_cash_flow_fy(ff).iloc[-1] == pytest.approx(170.0)        # 220-50
    assert fcf_margin_fy(ff).iloc[-1] == pytest.approx(170 / 1200)
    # FCF yield at $2bn market cap = 170/2000 = 8.5%
    assert fcf_yield_fy(ff, market_cap=2000).iloc[0] == pytest.approx(0.085)


def test_net_debt_and_leverage():
    ff = _ff(INDUSTRIAL_ROWS)
    nd_23 = net_debt_fy(ff).iloc[-1]                                     # 500-80 = 420
    assert nd_23 == pytest.approx(420.0)
    # No D&A in this fixture → EBITDA falls back to EBIT (260): 420 / 260.
    assert net_debt_to_ebitda_fy(ff).iloc[-1] == pytest.approx(420 / 260)
    # Interest coverage = 260 / 20 = 13x
    assert interest_coverage_fy(ff).iloc[-1] == pytest.approx(13.0)


def test_ebitda_falls_back_to_ebit_without_dna():
    ff = _ff(INDUSTRIAL_ROWS)
    assert ebitda_fy(ff).iloc[-1] == pytest.approx(260.0)  # EBIT only


def test_ebitda_adds_back_dna_when_present():
    rows = INDUSTRIAL_ROWS + [
        {"concept": "DepreciationAndAmortization", "period": "2022-12-31", "value": 50.0, "fy": 2022},
        {"concept": "DepreciationAndAmortization", "period": "2023-12-31", "value": 60.0, "fy": 2023},
    ]
    ff = _ff(rows)
    # EBITDA 2023 = EBIT 260 + D&A 60 = 320.
    assert ebitda_fy(ff).iloc[-1] == pytest.approx(320.0)
    # net debt / EBITDA now uses the real add-back: 420 / 320.
    assert net_debt_to_ebitda_fy(ff).iloc[-1] == pytest.approx(420 / 320)


def test_roic_and_roe_use_average_capital():
    ff = _ff(INDUSTRIAL_ROWS)
    # NOPAT 2023 = 260 * (1 - 0.21) = 205.4
    # IC 2022 = 400 + 100 + 800 - 50 = 1250
    # IC 2023 = 400 + 100 + 900 - 80 = 1320
    # avg IC = 1285 → ROIC = 205.4 / 1285 ≈ 0.1599
    expected_roic = (260 * 0.79) / ((1250 + 1320) / 2)
    assert roic_fy(ff).iloc[-1] == pytest.approx(expected_roic, rel=1e-6)
    # ROE 2023 = 160 / avg(800, 900) = 160 / 850
    assert roe_fy(ff).iloc[-1] == pytest.approx(160 / 850)


def test_pe_handles_negative_earnings():
    rows = INDUSTRIAL_ROWS + [
        {"concept": "NetIncomeLoss", "period": "2024-12-31", "value": -10.0, "fy": 2024},
    ]
    ff = _ff(rows)
    pe = pe_fy(ff, market_cap=2000)
    # Most-recent NI is negative → NaN
    assert math.isnan(pe.iloc[-1])


# --- Bank fixture --------------------------------------------------------- #
BANK_ROWS = [
    {"concept": "NetIncomeLoss",            "period": "2022-12-31", "value": 300.0, "fy": 2022},
    {"concept": "NetIncomeLoss",            "period": "2023-12-31", "value": 350.0, "fy": 2023},
    {"concept": "StockholdersEquity",       "period": "2022-12-31", "value": 3000.0, "fy": 2022},
    {"concept": "StockholdersEquity",       "period": "2023-12-31", "value": 3200.0, "fy": 2023},
    {"concept": "Goodwill",                 "period": "2022-12-31", "value": 400.0, "fy": 2022},
    {"concept": "Goodwill",                 "period": "2023-12-31", "value": 400.0, "fy": 2023},
    {"concept": "IntangibleAssetsNet",      "period": "2022-12-31", "value": 100.0, "fy": 2022},
    {"concept": "IntangibleAssetsNet",      "period": "2023-12-31", "value": 100.0, "fy": 2023},
    {"concept": "NetInterestIncome",        "period": "2023-12-31", "value": 500.0, "fy": 2023},
    {"concept": "Deposits",                 "period": "2022-12-31", "value": 35000.0, "fy": 2022},
    {"concept": "Deposits",                 "period": "2023-12-31", "value": 36000.0, "fy": 2023},
    {"concept": "Assets",                   "period": "2022-12-31", "value": 40000.0, "fy": 2022},
    {"concept": "Assets",                   "period": "2023-12-31", "value": 42000.0, "fy": 2023},
]


def test_is_bank_requires_deposits():
    """Deposits is the cleanest single-tag bank signal. CECL allowances and
    netted interest income show up at non-bank industrials too, so the old
    'NII OR Deposits OR Allowance' heuristic caught false positives like
    Lincoln Electric (Brad's live screen surfaced it)."""
    assert is_bank(_ff(BANK_ROWS)) is True
    assert is_bank(_ff(INDUSTRIAL_ROWS)) is False
    # Industrial reporting a CECL credit-loss allowance on trade receivables
    # is NOT a bank — must be excluded.
    industrial_with_cecl = INDUSTRIAL_ROWS + [
        {"concept": "AllowanceForLoanAndLeaseLosses",
         "period": "2023-12-31", "value": 5.0, "fy": 2023},
        {"concept": "NetInterestIncome",
         "period": "2023-12-31", "value": 10.0, "fy": 2023},
    ]
    assert is_bank(_ff(industrial_with_cecl)) is False


def test_rotce_and_tbv():
    ff = _ff(BANK_ROWS)
    # TCE 2022 = 3000-400-100 = 2500; TCE 2023 = 3200-400-100 = 2700; avg=2600
    # ROTCE 2023 = 350 / 2600 ≈ 0.1346
    assert rotce_fy(ff).iloc[-1] == pytest.approx(350 / 2600)
    assert tangible_book_value_fy(ff).iloc[-1] == pytest.approx(2700.0)


def test_percentile_vs_history():
    # 10 historical observations, current at the high end
    hist = pd.Series([0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13])
    assert percentile_vs_history(hist, 0.13) == pytest.approx(95.0)   # tied at top
    assert percentile_vs_history(hist, 0.04) == pytest.approx(5.0)    # tied at bottom
    assert percentile_vs_history(hist, 0.08) == pytest.approx(45.0)
    # Too few observations → NaN
    assert math.isnan(percentile_vs_history(pd.Series([0.05, 0.06]), 0.05))

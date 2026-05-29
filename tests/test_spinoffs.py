"""Offline tests for Phase 4 discovery + three-attribute / downside flags.

No network: monkeypatch EdgarProvider._get to return hand-built fixtures shaped
like real SEC payloads, and build SpinCo FinancialsFrames in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.base import FinancialsFrame
from sycamore_prep.adapters.edgar import EdgarProvider
from sycamore_prep.spinoffs import apply_flags, scan_recent_form10s, track_parent
from sycamore_prep.spinoffs.discovery import (
    SpinoffRecord,
    Status,
    _record_from_submissions,
    status_from_submissions,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _ff(series_by_concept: dict[str, list[tuple[str, float]]]) -> FinancialsFrame:
    """Build a FY FinancialsFrame from {concept: [(period_end, value), ...]}."""
    rows = []
    for concept, pts in series_by_concept.items():
        for period, val in pts:
            yr = int(period[:4])
            rows.append({
                "ticker": "SPN", "concept": concept, "period": period,
                "fy": yr, "fp": "FY", "form": "10-K", "value": float(val),
                "unit": "USD", "source": "edgar (primary)",
                "period_start": f"{yr - 1}-12-31", "period_days": 365,
            })
    return FinancialsFrame(pd.DataFrame(rows))


@pytest.fixture
def provider(tmp_path: Path, monkeypatch) -> EdgarProvider:
    subs = _load("submissions_min.json")
    hits = _load("efts_hits_min.json")
    ticker_map = {
        "0": {"cik_str": 313616, "ticker": "DHR", "title": "DANAHER CORP /DE/"},
        "1": {"cik_str": 1964738, "ticker": "SOLV", "title": "Solventum Corp"},
    }

    def fake_get(self, url: str):  # noqa: ARG001
        if "company_tickers.json" in url:
            return ticker_map
        if "submissions/CIK" in url:
            return subs
        if "efts.sec.gov" in url:
            return hits
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setattr(EdgarProvider, "_get", fake_get)
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    return EdgarProvider(user_agent="test/0.1 test@example.com", rate_limit_rps=1000)


# --------------------------------------------------------------------------- #
# flags
# --------------------------------------------------------------------------- #

def test_apply_flags_surfaces_downside():
    ff = _ff({
        "Revenues": [("2021-12-31", 1050), ("2022-12-31", 1000),
                     ("2023-12-31", 950), ("2024-12-31", 900)],
        "OperatingIncomeLoss": [("2022-12-31", 100), ("2023-12-31", 90), ("2024-12-31", 80)],
        "InterestExpense": [("2022-12-31", 40), ("2023-12-31", 45), ("2024-12-31", 50)],
        "DepreciationAndAmortization": [("2022-12-31", 50), ("2023-12-31", 50), ("2024-12-31", 50)],
        "LongTermDebt": [("2022-12-31", 600), ("2023-12-31", 600), ("2024-12-31", 600)],
        "CashAndEquivalents": [("2022-12-31", 50), ("2023-12-31", 50), ("2024-12-31", 50)],
        "StockholdersEquity": [("2022-12-31", 30), ("2023-12-31", 5), ("2024-12-31", -20)],
        "OperatingCashFlow": [("2022-12-31", 60), ("2023-12-31", 55), ("2024-12-31", 50)],
        "CapEx": [("2022-12-31", 40), ("2023-12-31", 42), ("2024-12-31", 45)],
    })
    rec = apply_flags(SpinoffRecord(spinco_ticker="SPN"), ff)

    assert rec.has_financials is True
    # net debt 550 / EBITDA 130 ≈ 4.23x
    assert rec.net_debt_ebitda is not None and rec.net_debt_ebitda > 4.0
    assert rec.interest_coverage is not None and rec.interest_coverage < 3.0
    assert rec.negative_equity is True
    assert set(rec.downside_flags) == {
        "high_leverage", "thin_interest_coverage", "negative_equity",
        "negative_or_thin_fcf", "declining_revenue",
    }
    # Three attributes reported separately, never collapsed.
    assert "net debt/EBITDA" in rec.q1_better_business
    assert rec.q3_improving_fundamentals.startswith("revenue 3y CAGR")


def test_apply_flags_pending_without_financials():
    rec = apply_flags(SpinoffRecord(spinco_name="Freshly Spun Inc"), None)
    assert rec.has_financials is False
    assert rec.q1_better_business == "pending"
    assert rec.q3_improving_fundamentals == "pending"
    assert rec.downside_flags == []
    assert rec.net_debt_ebitda is None  # never a silent zero


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #

def test_scan_groups_hits_and_sets_status(provider: EdgarProvider):
    recs = scan_recent_form10s(
        provider, forms=["10-12B"], lookback_days=540, query="information statement"
    )
    assert len(recs) == 2
    by_cik = {r.spinco_cik: r for r in recs}

    ws = by_cik["0001968487"]
    assert ws.spinco_name == "Worthington Steel, Inc."
    assert ws.spinco_ticker == "WS"
    assert ws.amendment_count == 1
    assert ws.status == Status.AMENDED.value
    assert ws.first_form10_date == "2023-10-26"
    assert ws.sic == "3310"
    assert all(u.startswith("https://www.sec.gov/Archives/") for u in ws.filing_urls)

    res = by_cik["0002039497"]
    assert res.spinco_ticker is None
    assert res.amendment_count == 0
    assert res.status == Status.FORM10_FILED.value


def test_status_from_submissions_upgrades_to_completed(provider: EdgarProvider):
    subs = provider.get_submissions("0001964738")  # SpinCo with its own 10-K
    assert status_from_submissions(subs, Status.AMENDED.value) == Status.COMPLETED.value
    # An empty frame leaves the base status untouched.
    assert status_from_submissions(subs.iloc[0:0], Status.FORM10_FILED.value) == Status.FORM10_FILED.value


def test_record_from_submissions_links_dates_and_status(provider: EdgarProvider):
    subs = provider.get_submissions("0001964738")
    rec = _record_from_submissions(subs, "Solventum Corp", "0001964738", "SOLV")
    assert rec.spinco_name == "Solventum Corp"
    assert rec.amendment_count == 1
    assert rec.first_form10_date == "2023-11-01"
    assert rec.latest_amendment_date == "2024-03-15"
    assert rec.status == Status.COMPLETED.value  # has a standalone 10-K
    assert any(u.endswith("d10b.htm") for u in rec.filing_urls)


def test_track_parent_explicit_spinco(provider: EdgarProvider):
    recs = track_parent(provider, "DHR", spinco="SOLV")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.parent_ticker == "DHR"
    assert rec.parent_name == "DANAHER CORP /DE/"
    assert rec.spinco_ticker == "SOLV"
    assert rec.status == Status.COMPLETED.value
    assert rec.amendment_count == 1
    assert rec.sources == "edgar (primary)"

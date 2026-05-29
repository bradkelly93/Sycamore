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
from sycamore_prep.spinoffs import apply_flags, scan_recent_form10s, softfields, track_parent
from sycamore_prep.spinoffs.discovery import (
    SpinoffRecord,
    Status,
    _record_from_submissions,
    status_from_submissions,
)
from sycamore_prep.spinoffs.report import records_to_df, write_tracker_md, write_tracker_xlsx
from sycamore_prep.spinoffs.tracker import run_track

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


# A high-leverage, declining, negative-equity SpinCo — trips every computable
# downside flag. Reused by the flag, report and end-to-end tests.
_LEVERED_FF = _ff({
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

# Real information-statement phrasing captured by precheck_infostmt.py.
# Note: the date PRECEDES the "record date" anchor, and the distribution date
# hangs off "will be distributed ... on" — the exact shapes that broke the
# first-cut regexes.
_INFO_STMT = (  # Danaher -> Veralto (1:3, record 2023-09-13, distributed 2023-09-30)
    "The board of directors of Danaher approved the distribution of all of the "
    "outstanding shares of Veralto common stock to holders of Danaher common stock. "
    "Each Danaher stockholder will receive one share of Veralto common stock for "
    "every three shares of Danaher common stock held at the close of business on "
    "September 13, 2023, the record date for the distribution. It is expected that "
    "all of the shares of Veralto common stock will be distributed by Danaher on "
    "September 30, 2023, to holders of record of Danaher common stock."
)
_SOLV_INFO = (  # 3M -> Solventum (1:4, record 2024-03-18, distributed 2024-04-01)
    "Each 3M shareholder as of the close of business on March 18, 2024, the record "
    "date for the distribution, will receive one share of Solventum common stock for "
    "every four shares of 3M common stock held by such shareholder. It is expected "
    "that the distribution will occur at 3:30 a.m., Eastern Time, on April 1, 2024, "
    "to holders of record of 3M common stock."
)


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


# --------------------------------------------------------------------------- #
# soft-field extractor (fail-safe)
# --------------------------------------------------------------------------- #

def test_softfields_extracts_veralto_terms():
    sf = softfields.extract(_INFO_STMT)
    assert sf.distribution_ratio == "1:3"          # date PRECEDES the anchor
    assert sf.record_date == "2023-09-13"
    assert sf.distribution_date == "2023-09-30"    # "will be distributed ... on"
    assert sf.source == "derived (parsed 10-12B)"


def test_softfields_extracts_solventum_terms():
    sf = softfields.extract(_SOLV_INFO)
    assert sf.distribution_ratio == "1:4"
    assert sf.record_date == "2024-03-18"
    assert sf.distribution_date == "2024-04-01"    # "distribution will occur ... on"


def test_softfields_failsafe_on_no_match():
    sf = softfields.extract("This document discusses governance and contains no spin terms.")
    assert sf.distribution_ratio is None
    assert sf.record_date is None and sf.distribution_date is None
    assert sf.source is None


def test_softfields_ambiguous_ratio_is_pending():
    txt = ("one share for every two shares of Parent held. Separately, one share "
           "for every three shares of Parent held.")
    assert softfields.extract(txt).distribution_ratio is None  # conflicting -> pending


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def test_report_renders_pending_and_writes(tmp_path: Path):
    from types import SimpleNamespace

    from openpyxl import load_workbook

    levered = apply_flags(
        SpinoffRecord(spinco_name="LeveredCo", spinco_ticker="LVR", status="completed"),
        _LEVERED_FF,
    )
    levered.q2_valuation_disparity = "trades as LVR; run `comps LVR`"
    pending = SpinoffRecord(spinco_name="FreshCo", status="form-10-filed",
                            q1_better_business="pending", q3_improving_fundamentals="pending")
    df = records_to_df([levered, pending])
    assert {"downside_flags", "q1_better_business", "q2_valuation_disparity",
            "q3_improving_fundamentals"}.issubset(df.columns)

    result = SimpleNamespace(records=[levered, pending], df=df, mode="track")
    write_tracker_xlsx(result, tmp_path / "t.xlsx")
    write_tracker_md(result, tmp_path / "t.md")
    assert (tmp_path / "t.xlsx").exists()
    md = (tmp_path / "t.md").read_text()
    assert "Downside flags" in md and "pending" in md

    wb = load_workbook(tmp_path / "t.xlsx")
    assert {"tracker", "downside_flags", "spinco_financials", "filings", "sources"}.issubset(
        set(wb.sheetnames)
    )


# --------------------------------------------------------------------------- #
# tracker end-to-end (offline)
# --------------------------------------------------------------------------- #

def test_run_track_end_to_end(tmp_path: Path, monkeypatch):
    subs = _load("submissions_min.json")
    ticker_map = {
        "0": {"cik_str": 313616, "ticker": "DHR", "title": "DANAHER CORP /DE/"},
        "1": {"cik_str": 1964738, "ticker": "SOLV", "title": "Solventum Corp"},
    }

    def fake_get(self, url: str):  # noqa: ARG001
        if "company_tickers.json" in url:
            return ticker_map
        if "submissions/CIK" in url:
            return subs
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setattr(EdgarProvider, "_get", fake_get)
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(EdgarProvider, "get_financials",
                        lambda self, ticker, use_cache=True: _LEVERED_FF)
    # The terms live in the EX-99.1, not the cover: index lists both; the
    # tracker must resolve the exhibit-99 doc before parsing.
    monkeypatch.setattr(EdgarProvider, "get_filing_index", lambda self, url, use_cache=True: [
        {"name": "0001-index.html", "size": 500},
        {"name": "cover-10x12b.htm", "size": 12000},
        {"name": "exhibit991-info.htm", "size": 900000},
    ])
    monkeypatch.setattr(EdgarProvider, "get_filing_text",
                        lambda self, url, use_cache=True: _INFO_STMT)

    res = run_track("DHR", spinco="SOLV", output_path=tmp_path / "sp.xlsx")

    assert (tmp_path / "sp.xlsx").exists() and (tmp_path / "sp.md").exists()
    assert len(res.records) == 1
    r = res.records[0]
    assert r.parent_ticker == "DHR" and r.spinco_ticker == "SOLV"
    assert r.status == Status.COMPLETED.value
    assert "high_leverage" in r.downside_flags
    # resolved the EX-99.1 information statement (not the cover)
    assert r.information_statement_url.endswith("exhibit991-info.htm")
    # soft fields parsed from the (mocked) information statement
    assert r.distribution_ratio == "1:3"
    assert r.record_date == "2023-09-13" and r.distribution_date == "2023-09-30"
    # both source tags present; three attributes reported separately
    assert "edgar (primary)" in r.sources and "derived (parsed 10-12B)" in r.sources
    assert r.q2_valuation_disparity.startswith("trades as SOLV")
    assert r.review_flags  # qualitative "read the Form 10" prompts present

"""Offline tests for the Phase 4 filings adapter surface.

Mirrors tests/test_edgar_integration.py: monkeypatch EdgarProvider._get to
return hand-built fixtures shaped like real SEC payloads (captured from the
data-shape pre-check), and redirect the cache to tmp_path. No network.

Fixtures:
- submissions_min.json — a SpinCo's filings.recent with a 10-12B, a 10-12B/A,
  an 8-K (item 2.01 = disposition/completion) and a later 10-K.
- efts_hits_min.json — two full-text-search hits: one with a ticker
  (Worthington Steel / WS) and one without (a not-yet-trading SpinCo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.edgar import EdgarProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture
def provider(tmp_path: Path, monkeypatch) -> EdgarProvider:
    subs = _load("submissions_min.json")
    hits = _load("efts_hits_min.json")

    def fake_get(self, url: str):  # noqa: ARG001 — bound method consumes self
        if "submissions/CIK" in url:
            return subs
        if "efts.sec.gov" in url:
            return hits
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setattr(EdgarProvider, "_get", fake_get)
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    return EdgarProvider(user_agent="test/0.1 test@example.com", rate_limit_rps=1000)


def test_get_submissions_flattens_links_and_tags(provider: EdgarProvider, tmp_path: Path):
    df = provider.get_submissions("0001964738")

    # Schema + source tag.
    for col in ["cik", "ticker", "form", "filing_date", "accession", "filing_url", "source"]:
        assert col in df.columns
    assert (df["source"] == "edgar (primary)").all()
    assert len(df) == 4

    # Ticker backfilled from raw `tickers` when a CIK is passed.
    assert df["ticker"].iloc[0] == "SOLV"

    # The 10-12B row, with an audit URL built from the SUBJECT cik (not the
    # filing-agent accession prefix).
    f10 = df[df["form"] == "10-12B"]
    assert len(f10) == 1
    url = f10.iloc[0]["filing_url"]
    assert "edgar/data/1964738/" in url
    assert url.endswith("d10b.htm")
    assert "000119312523250001" in url  # dashes stripped from accession

    # 8-K item codes preserved as a comma-joined string.
    eightk = df[df["form"] == "8-K"].iloc[0]
    assert eightk["items"] == "2.01,9.01"
    assert bool(eightk["is_xbrl"]) is True
    assert bool(f10.iloc[0]["is_xbrl"]) is False

    # Empty reportDate renders as missing, never "".
    assert pd.isna(f10.iloc[0]["report_date"])

    # Raw payload cached as a JSON sidecar.
    assert (tmp_path / "submissions_0001964738.json").exists()


def test_get_submissions_served_from_cache(provider: EdgarProvider, monkeypatch):
    provider.get_submissions("0001964738")

    def boom(self, url: str):  # noqa: ARG001
        raise AssertionError(f"Cache miss — got HTTP call for {url}")

    monkeypatch.setattr(EdgarProvider, "_get", boom)
    df2 = provider.get_submissions("0001964738")
    assert len(df2) == 4


def test_search_filings_parses_hits(provider: EdgarProvider):
    df = provider.search_filings(forms=["10-12B"], start="2023-01-01", end="2024-12-31")

    for col in ["cik", "name", "ticker", "form", "root_form", "accession", "filing_url", "source"]:
        assert col in df.columns
    assert (df["source"] == "edgar (primary)").all()
    assert len(df) == 2

    # Hit with a ticker.
    ws = df.iloc[0]
    assert ws["ticker"] == "WS"
    assert ws["cik"] == "0001968487"
    assert ws["form"] == "10-12B/A" and ws["root_form"] == "10-12B"
    assert ws["name"] == "Worthington Steel, Inc."
    assert ws["sic"] == "3310"
    assert "edgar/data/1968487/" in ws["filing_url"]
    assert ws["filing_url"].endswith("d465762d1012ba.htm")

    # Not-yet-trading SpinCo: no ticker (missing), name + CIK still parsed.
    res = df.iloc[1]
    assert pd.isna(res["ticker"])
    assert res["name"].startswith("Resolute Holdings")
    assert res["cik"] == "0002039497"


def test_search_filings_served_from_cache(provider: EdgarProvider, monkeypatch):
    provider.search_filings(forms=["10-12B"], start="2023-01-01", end="2024-12-31")

    def boom(self, url: str):  # noqa: ARG001
        raise AssertionError(f"Cache miss — got HTTP call for {url}")

    monkeypatch.setattr(EdgarProvider, "_get", boom)
    df2 = provider.search_filings(forms=["10-12B"], start="2023-01-01", end="2024-12-31")
    assert len(df2) == 2


def test_display_name_parser_variants():
    from sycamore_prep.adapters.edgar import _parse_display_name

    name, ticker, cik = _parse_display_name("Worthington Steel, Inc.  (WS)  (CIK 0001968487)")
    assert (name, ticker, cik) == ("Worthington Steel, Inc.", "WS", "0001968487")

    name, ticker, cik = _parse_display_name("Resolute Holdings Management, Inc.  (CIK 0002039497)")
    assert name == "Resolute Holdings Management, Inc."
    assert ticker is None and cik == "0002039497"

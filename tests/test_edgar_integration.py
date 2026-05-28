"""End-to-end EdgarProvider test against a real-shape SEC payload fixture.

This is the closest we can get to a live SEC pull without leaving the sandbox:
- ticker → CIK resolution exercises the real `company_tickers.json` shape
- companyfacts JSON matches SEC's actual schema (label/description/units arrays
  with start/end/val/accn/fy/fp/form/filed keys, including a restatement and
  a quarterly row)
- the test asserts: schema, source tag, restatement dedupe, cache write, and
  that a second call served from cache does NOT touch the network layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters import edgar as edgar_mod
from sycamore_prep.adapters.edgar import EdgarProvider


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture
def patched_provider(tmp_path: Path, monkeypatch) -> EdgarProvider:
    """EdgarProvider with HTTP and cache redirected to fixtures + tmp_path."""
    ticker_map = _load("ticker_map_min.json")
    facts = _load("companyfacts_cw_min.json")

    def fake_get(self, url: str):  # noqa: ARG001 — `self` consumed by bound method
        if "company_tickers.json" in url:
            return ticker_map
        if "companyfacts/CIK" in url:
            return facts
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setattr(EdgarProvider, "_get", fake_get)
    monkeypatch.setattr(edgar_mod, "cache", cache_mod)  # ensure same module
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)

    return EdgarProvider(user_agent="test/0.1 test@example.com", rate_limit_rps=1000)


def test_resolve_ticker_to_cik(patched_provider: EdgarProvider):
    meta = patched_provider.get_company_meta("CW")
    assert meta.cik == "0000026324"
    assert "CURTISS" in meta.name.upper()
    assert meta.source == "edgar (primary)"


def test_full_pipeline_parses_caches_and_serves_from_cache(
    patched_provider: EdgarProvider, tmp_path: Path, monkeypatch
):
    ff = patched_provider.get_financials("CW")

    # Schema enforced.
    for col in ["ticker", "concept", "period", "fy", "fp", "form",
                "value", "unit", "source"]:
        assert col in ff.df.columns

    # All rows tagged primary.
    assert (ff.df["source"] == "edgar (primary)").all()

    # Restatement winner: FY2022 Revenues should be 2,557M (later `filed`),
    # not the original 2,554M.
    rev_2022 = ff.df.query(
        "concept == 'Revenues' and fy == 2022 and fp == 'FY'"
    )
    assert len(rev_2022) == 1
    assert rev_2022.iloc[0]["value"] == 2557000000.0

    # Synonym fallback: CostOfGoodsAndServicesSold → canonical CostOfRevenue.
    assert "CostOfRevenue" in set(ff.df["concept"])

    # FinancialsFrame.latest() returns the most recent FY value.
    assert ff.latest("Revenues") == 2840000000.0
    assert ff.latest("NetIncomeLoss") == 333000000.0
    assert ff.latest("EpsDiluted") == 8.69

    # Quarterly row survived and is queryable.
    q1 = ff.concept("NetIncomeLoss", fp="Q1")
    assert len(q1) == 1 and q1.iloc[0]["value"] == 71000000.0

    # Cache file was written.
    cached_path = tmp_path / "financials_CW.parquet"
    assert cached_path.exists()

    # Second call must NOT hit HTTP — flip _get to raise.
    def boom(self, url: str):  # noqa: ARG001
        raise AssertionError(f"Cache miss — got HTTP call for {url}")

    monkeypatch.setattr(EdgarProvider, "_get", boom)
    ff2 = patched_provider.get_financials("CW")
    assert len(ff2.df) == len(ff.df)
    assert ff2.latest("Revenues") == 2840000000.0


def test_refresh_bypasses_cache(patched_provider: EdgarProvider, tmp_path: Path, monkeypatch):
    # First pull populates cache.
    patched_provider.get_financials("CW")
    assert (tmp_path / "financials_CW.parquet").exists()

    # use_cache=False must re-hit HTTP. Detect by counting calls.
    calls = {"n": 0}
    original = EdgarProvider._get

    def counting(self, url):
        calls["n"] += 1
        return original(self, url)

    # We need the patched fake_get from the fixture, not the original.
    # Patch by wrapping the currently-installed _get.
    current = EdgarProvider._get

    def wrapper(self, url):
        calls["n"] += 1
        return current(self, url)

    monkeypatch.setattr(EdgarProvider, "_get", wrapper)
    patched_provider.get_financials("CW", use_cache=False)
    assert calls["n"] >= 1, "Refresh should have made at least one HTTP call."

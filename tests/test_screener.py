"""Screener-level tests: scoring direction, negative-space filter, and an
end-to-end run with two synthetic tickers monkey-patched into the adapters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.edgar import EdgarProvider
from sycamore_prep.screener import run_screener
from sycamore_prep.screener.scoring import score_universe


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_scoring_directions_are_correct():
    df = pd.DataFrame({
        # Higher-is-better: clear winner gets higher quality score.
        "roic": [0.20, 0.05, 0.10],
        "fcf_margin": [0.15, 0.02, 0.08],
        # Lower-is-better: lowest leverage wins.
        "net_debt_ebitda": [1.0, 5.0, 2.5],
        # Valuation: higher FCF yield = cheaper = better.
        "fcf_yield": [0.08, 0.02, 0.05],
        # Lower P/E = better.
        "pe": [12.0, 25.0, 18.0],
    }, index=["GOOD", "BAD", "MID"])

    res = score_universe(df)
    out = res.df.sort_values("composite_rank")
    # GOOD must rank first, BAD last.
    assert out.index[0] == "GOOD"
    assert out.index[-1] == "BAD"
    # Sub-scores reported separately.
    for col in ["q1_quality_score", "q2_valuation_score", "q3_improving_score",
                "composite_score", "composite_rank"]:
        assert col in out.columns


def test_scoring_tolerates_missing_components():
    df = pd.DataFrame({
        "roic": [0.20, 0.05],
        # fcf_yield missing entirely.
    }, index=["A", "B"])
    res = score_universe(df)
    # A has higher ROIC → higher quality → ranks first.
    assert res.df.loc["A", "composite_rank"] == 1


def _fake_edgar_get(self, url: str):  # noqa: ARG001 — first arg is `self`
    if "company_tickers.json" in url:
        return {
            "0": {"cik_str": 26324, "ticker": "AAA", "title": "ALPHA INC"},
            "1": {"cik_str": 1001, "ticker": "BBB", "title": "BETA CORP"},
        }
    # Two synthetic companyfacts payloads — AAA has stronger fundamentals.
    if "CIK0000026324" in url:
        return _facts(rev=(1000, 1200), op=(200, 260), ni=(120, 160),
                      cfo=(180, 220), capex=(40, 50),
                      eq=(800, 900), lt_debt=(400, 400), cash=(50, 80))
    return _facts(rev=(900, 920), op=(50, 40), ni=(20, -5),
                  cfo=(60, 50), capex=(40, 45),
                  eq=(500, 480), lt_debt=(600, 650), cash=(20, 10))


def _facts(*, rev, op, ni, cfo, capex, eq, lt_debt, cash):
    def series(tag, vals, unit="USD"):
        return {
            "units": {
                unit: [
                    {"end": "2022-12-31", "val": vals[0], "fy": 2022, "fp": "FY",
                     "form": "10-K", "filed": "2023-02-15"},
                    {"end": "2023-12-31", "val": vals[1], "fy": 2023, "fp": "FY",
                     "form": "10-K", "filed": "2024-02-15"},
                ]
            }
        }
    return {
        "facts": {"us-gaap": {
            "Revenues": series("Revenues", rev),
            "OperatingIncomeLoss": series("op", op),
            "NetIncomeLoss": series("ni", ni),
            "NetCashProvidedByUsedInOperatingActivities": series("cfo", cfo),
            "PaymentsToAcquirePropertyPlantAndEquipment": series("capex", capex),
            "StockholdersEquity": series("eq", eq),
            "LongTermDebt": series("lt", lt_debt),
            "CashAndCashEquivalentsAtCarryingValue": series("cash", cash),
            "InterestExpense": series("ie", (20, 25)),
            "CostOfGoodsAndServicesSold": series("cogs",
                                                 (rev[0]*0.6, rev[1]*0.6)),
        }}
    }


@pytest.fixture
def patched_adapters(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(EdgarProvider, "_get", _fake_edgar_get)
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    # Disable yfinance — we'll pass --skip-market-cap to keep this offline.
    yield tmp_path


def test_run_screener_end_to_end_offline(patched_adapters: Path):
    out = run_screener(
        tickers=["AAA", "BBB"],
        skip_market_cap=True,
        output_path=patched_adapters / "screener_output.xlsx",
    )
    assert (patched_adapters / "screener_output.xlsx").exists()
    assert set(out.index) == {"AAA", "BBB"}
    # AAA has stronger quality (higher ROIC, lower leverage) and is clean;
    # BBB has negative earnings / low FCF so it's flagged and sorted last.
    assert out.loc["AAA", "composite_rank"] == 1
    assert out.loc["BBB", "composite_rank"] == 2
    # Sub-scores reported separately (the headline CLAUDE.md requirement).
    for col in ["q1_quality_score", "q2_valuation_score", "q3_improving_score"]:
        assert col in out.columns
    # Sources tagged.
    assert "edgar (primary)" in out.loc["AAA", "sources"]


def test_default_keeps_flagged_names_visible_but_downranked(patched_adapters: Path):
    out = run_screener(
        tickers=["AAA", "BBB"],
        skip_market_cap=True,
        output_path=patched_adapters / "screener_keep.xlsx",
    )
    # BBB trips a negative-space flag (negative NI / low FCF) but is KEPT,
    # with sub-scores still populated and a (worse) rank — never hidden.
    assert bool(out.loc["BBB", "negative_space"]) is True
    assert out.loc["BBB", "ns_flags"]                       # non-empty
    assert pd.notna(out.loc["BBB", "composite_rank"])       # still ranked
    assert pd.notna(out.loc["BBB", "q1_quality_score"])     # sub-score visible
    # Clean name AAA outranks the flagged BBB regardless of raw composite.
    assert out.loc["AAA", "composite_rank"] < out.loc["BBB", "composite_rank"]


def test_hard_exclude_drops_flagged_from_ranking(patched_adapters: Path):
    out = run_screener(
        tickers=["AAA", "BBB"],
        skip_market_cap=True,
        output_path=patched_adapters / "screener_hard.xlsx",
        hard_exclude_neg_space=True,
    )
    # Under hard-exclude, BBB stays in the output (flags visible) but is
    # removed from the ranking (composite_rank NaN).
    assert "BBB" in out.index
    assert bool(out.loc["BBB", "negative_space"]) is True
    assert pd.isna(out.loc["BBB", "composite_rank"])
    # The clean name still gets a real rank.
    assert out.loc["AAA", "composite_rank"] == 1

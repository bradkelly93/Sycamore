"""Screener-level tests: scoring direction, negative-space filter, and an
end-to-end run with two synthetic tickers monkey-patched into the adapters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.csv_screen import CsvScreenProvider
from sycamore_prep.adapters.edgar import EdgarProvider
from sycamore_prep.adapters.tradingview import SOURCE_TAG, TradingViewProvider
from sycamore_prep.config import load_config
from sycamore_prep.screener import run_screener
from sycamore_prep.screener import screen as screen_mod
from sycamore_prep.screener.scoring import score_universe
from sycamore_prep.screener.screen import _tv_divergence, _tv_provider


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


# ---- TradingView overlay (NON-PRIMARY context; must never touch the score) ----

def _fake_tv_df():
    # Only AAA is in the screen result; BBB is absent (i.e. fails the screen).
    return pd.DataFrame({
        "ticker": ["AAA"],
        "RSI": [55.0],
        "close": [10.0],
        "passes_screen": [True],
        "asof": ["2026-05-30T00:00:00+00:00"],
        "source": [SOURCE_TAG],
    })


class _StubProvider:
    """Stand-in technical-screen provider so overlay tests stay mode-agnostic."""

    def __init__(self, frame=None, boom=False):
        self._frame = frame
        self._boom = boom

    def get_screen(self, tickers=None, refresh: bool = False):
        if self._boom:
            raise RuntimeError("tradingview endpoint down")
        return self._frame


def test_overlay_does_not_change_composite(patched_adapters: Path, monkeypatch):
    monkeypatch.setattr(screen_mod, "_tv_provider",
                        lambda mode: _StubProvider(_fake_tv_df()))
    base = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "base.xlsx",
    )
    over = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "over.xlsx", tv_overlay=True,
    )
    # The pure fundamental engine is byte-identical with vs without the overlay.
    for col in ["composite_score", "composite_rank",
                "q1_quality_score", "q2_valuation_score", "q3_improving_score"]:
        pd.testing.assert_series_equal(
            base[col].sort_index(), over[col].sort_index(), check_names=False
        )
    # Overlay columns appear only in the overlaid run.
    assert "passes_screen" in over.columns and "passes_screen" not in base.columns
    assert bool(over.loc["AAA", "passes_screen"]) is True
    assert bool(over.loc["BBB", "passes_screen"]) is False
    # Carried indicator present for the passer, blank for the non-passer.
    assert over.loc["AAA", "tv_RSI"] == 55.0
    assert pd.isna(over.loc["BBB", "tv_RSI"])
    # Source tag appended for the passer only.
    assert SOURCE_TAG in over.loc["AAA", "sources"]
    assert SOURCE_TAG not in (over.loc["BBB", "sources"] or "")


def test_overlay_columns_are_in_tail(patched_adapters: Path, monkeypatch):
    monkeypatch.setattr(screen_mod, "_tv_provider",
                        lambda mode: _StubProvider(_fake_tv_df()))
    out = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "tail.xlsx", tv_overlay=True,
    )
    cols = list(out.columns)
    ns = cols.index("ns_flags")  # downside flags stay left of every overlay col
    for c in ["passes_screen", "tv_divergence", "tv_RSI", "tv_asof"]:
        assert cols.index(c) > ns


def test_overlay_resilient_on_tv_failure(patched_adapters: Path, monkeypatch):
    monkeypatch.setattr(screen_mod, "_tv_provider",
                        lambda mode: _StubProvider(boom=True))
    out = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "fail.xlsx", tv_overlay=True,
    )
    # Full fundamental output survives; the overlay columns are simply absent.
    assert out.loc["AAA", "composite_rank"] == 1
    assert pd.notna(out.loc["AAA", "q1_quality_score"])
    assert "passes_screen" not in out.columns
    # ... and the skip is an explicit, actionable note rather than a silent gap.
    note = out.attrs.get("tv_note")
    assert note and "skipped" in note and "tradingview endpoint down" in note


def test_tv_overlay_skips_with_clear_note_when_disabled(patched_adapters: Path, monkeypatch):
    """`--tv-overlay` with tradingview.enabled=False must not silently no-op:
    it degrades to the full fundamental screen plus an actionable note telling
    the analyst how to turn the overlay on (mirrors the pipeline's missing-
    universe message)."""
    disabled = load_config().model_copy(deep=True)
    disabled.tradingview.enabled = False
    monkeypatch.setattr(screen_mod, "load_config", lambda: disabled)
    out = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "tv_disabled.xlsx", tv_overlay=True,
    )
    # Full fundamental output, no TradingView columns, no traceback.
    assert out.loc["AAA", "composite_rank"] == 1
    assert "passes_screen" not in out.columns
    note = out.attrs.get("tv_note")
    assert note and "enabled" in note and "config.yaml" in note


def test_prediction_overlay_skips_with_clear_note_offline(patched_adapters: Path, monkeypatch):
    """`--with-prediction-overlay` with no confirmed mapping (egress blocked /
    empty CSV) degrades cleanly: full screen, blank event_* columns, and a clear,
    actionable skip note — never a traceback."""
    # Pin the mapping to a non-existent path so the overlay loads an empty
    # mapping and never touches the network, regardless of local data/raw/.
    monkeypatch.setattr(
        "sycamore_prep.prediction_markets.mapping.mapping_csv_path",
        lambda: patched_adapters / "no_such_mapping.csv",
    )
    out = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "pred.xlsx", with_prediction_overlay=True,
    )
    # Fundamental ranking intact; event columns present but blank.
    assert out.loc["AAA", "composite_rank"] == 1
    assert out.loc["BBB", "composite_rank"] == 2
    assert "event_top_prob" in out.columns
    assert out["event_top_prob"].isna().all()
    assert not out["event_contradiction"].any()
    # Actionable skip note that points at the fix.
    note = out.attrs.get("prediction_note")
    assert note and "prediction-discover" in note


def test_tv_provider_factory():
    from sycamore_prep.adapters.trend_regime import TrendRegimeProvider
    assert isinstance(_tv_provider("trend"), TrendRegimeProvider)
    assert isinstance(_tv_provider("csv"), CsvScreenProvider)
    assert isinstance(_tv_provider("api"), TradingViewProvider)
    assert isinstance(_tv_provider("anything-else"), TradingViewProvider)


def test_divergence_buckets():
    composite = pd.Series({"A": 90.0, "B": 80.0, "C": 20.0, "D": 10.0,
                           "E": float("nan"), "F": 50.0})
    passes = pd.Series({"A": True, "B": False, "C": True, "D": False,
                        "E": False, "F": True})
    has_tv = pd.Series({"A": True, "B": True, "C": True, "D": True,
                        "E": True, "F": False})
    out = _tv_divergence(composite, passes, has_tv, strong_pctile=0.6)
    assert out["A"] == "agree_strong"                      # strong + pass
    assert out["B"] == "diverge_fund_strong_tech_fail"     # strong + fail
    assert out["C"] == "diverge_fund_weak_tech_pass"       # weak + pass
    assert out["D"] == "agree_weak"                        # weak + fail
    assert out["E"] == "n/a"                               # NaN composite
    assert out["F"] == "no_tv"                             # membership unknown


# ---- Merge-correctness: the three NON-PRIMARY overlays are structurally walled
# off from the three-attribute score / composite / rank (CLAUDE.md). ----

def test_score_universe_ignores_overlay_columns():
    """score_universe must read ONLY the named fundamental components. Inject
    vol + TradingView + prediction overlay columns whose values would flip the
    ranking IF they leaked in, and assert every score/rank is byte-identical."""
    base = pd.DataFrame({
        "roic": [0.20, 0.05, 0.10],
        "fcf_margin": [0.15, 0.02, 0.08],
        "pe": [12.0, 25.0, 18.0],
        "fcf_yield": [0.08, 0.02, 0.05],
        "rev_3yr_cagr": [0.10, 0.00, 0.05],
    }, index=["A", "B", "C"])
    polluted = base.copy()
    # Volatility overlay (annotated onto rows BEFORE scoring) ...
    polluted["iv_rank"] = [0.99, 0.01, 0.50]
    polluted["iv_percentile"] = [0.95, 0.05, 0.50]
    polluted["vol_beta"] = [3.0, 0.1, 1.0]
    polluted["liquidity_rating"] = [1.0, 4.0, 2.0]
    # ... TradingView overlay (spliced AFTER rank) ...
    polluted["passes_screen"] = [False, True, True]
    polluted["tv_RSI"] = [10.0, 90.0, 50.0]
    polluted["tv_divergence"] = ["x", "y", "z"]
    # ... prediction overlay (spliced AFTER rank).
    polluted["event_top_prob"] = [0.90, 0.00, 0.40]
    polluted["event_contradiction"] = [True, False, False]

    a = score_universe(base).df
    b = score_universe(polluted).df
    for col in ["q1_quality_score", "q2_valuation_score", "q3_improving_score",
                "composite_score", "composite_rank"]:
        pd.testing.assert_series_equal(a[col], b[col], check_names=False)


def test_all_overlays_off_vs_on_identical_composite_rank(patched_adapters, monkeypatch):
    """End-to-end: running the screener with all three overlay flags OFF vs ON
    yields IDENTICAL composite_rank (and sub-scores) for every name. The overlays
    splice columns but can never reach score_universe / composite / rank."""
    # TradingView is stubbed so the overlay genuinely splices columns (proving
    # the post-rank splice path runs). Vol + prediction run with their flags on
    # but degrade offline (no creds / empty mapping); the invariant holds either
    # way. Pin the prediction mapping to a non-existent path so the run is
    # hermetic + offline regardless of any local data/raw/ contents.
    monkeypatch.setattr(screen_mod, "_tv_provider",
                        lambda mode: _StubProvider(_fake_tv_df()))
    monkeypatch.setattr(
        "sycamore_prep.prediction_markets.mapping.mapping_csv_path",
        lambda: patched_adapters / "no_such_mapping.csv",
    )
    off = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "off.xlsx",
    )
    on = run_screener(
        tickers=["AAA", "BBB"], skip_market_cap=True,
        output_path=patched_adapters / "on.xlsx",
        with_vol=True, tv_overlay=True, with_prediction_overlay=True,
    )
    for col in ["composite_rank", "composite_score",
                "q1_quality_score", "q2_valuation_score", "q3_improving_score"]:
        pd.testing.assert_series_equal(
            off[col].sort_index(), on[col].sort_index(), check_names=False
        )
    # Not vacuous: the overlaid run actually gained overlay columns (TradingView
    # membership + prediction event columns), yet rank stayed identical ...
    assert "passes_screen" in on.columns and "passes_screen" not in off.columns
    assert "event_top_prob" in on.columns
    # ... and every overlay column stays to the RIGHT of the downside flags.
    cols = list(on.columns)
    ns = cols.index("ns_flags")
    assert cols.index("passes_screen") > ns
    assert cols.index("event_top_prob") > ns

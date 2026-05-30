"""Volatility overlay tests.

Three layers, all offline (no creds, no network):
  1. Pure metric math vs. hand-computed values (the CLAUDE.md quality bar).
  2. The tastytrade market-metrics parser + the provider (HTTP monkeypatched,
     cache redirected to tmp_path), mirroring the EdgarProvider test pattern.
  3. The overlay assembler + the screener's graceful annotation path.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.base import VolatilityFrame
from sycamore_prep.adapters.tastytrade_provider import (
    SOURCE_TAG,
    TastytradeProvider,
    parse_market_metrics,
)
from sycamore_prep.metrics.volatility import (
    expected_move_into_earnings,
    expected_move_pct,
    implied_downside_breaches_mos,
    iv_percentile_pct,
    iv_rank_pct,
    next_earnings_date,
    put_skew,
    sigma_down_price,
    volatility_overlay,
)
from sycamore_prep.screener.screen import ScreenerRow, _enrich_rows_with_vol


FIXTURE_DIR = Path(__file__).parent / "fixtures"
ASOF = "2026-05-30"


def _payload() -> dict:
    return json.loads((FIXTURE_DIR / "market_metrics_min.json").read_text())


def _frame() -> VolatilityFrame:
    return VolatilityFrame(parse_market_metrics(_payload(), as_of=ASOF))


# --------------------------------------------------------------------------
# 1. Pure math
# --------------------------------------------------------------------------
def test_expected_move_pct_full_year_equals_iv():
    # Over exactly one year the 1-sigma move equals the annualized IV.
    assert expected_move_pct(0.30, 365) == pytest.approx(0.30)


def test_expected_move_pct_quarter_year_halves():
    # 0.25 of a year -> sqrt(0.25) = 0.5 scaling.
    assert expected_move_pct(0.40, 365 * 0.25) == pytest.approx(0.20)


def test_expected_move_pct_rejects_bad_inputs():
    assert expected_move_pct(None, 30) is None
    assert expected_move_pct(0.30, None) is None
    assert expected_move_pct(-0.1, 30) is None
    assert expected_move_pct(0.30, -30) is None


def test_sigma_down_price_is_downside_leg():
    # 30% annualized vol over a year -> 1-sigma down lands at 70.
    assert sigma_down_price(100.0, 0.30, 365) == pytest.approx(70.0)


def test_put_skew_picks_25_delta_wings():
    iv_by_delta = {-0.25: 0.35, 0.25: 0.30, -0.10: 0.50, 0.50: 0.28}
    # 25d put (0.35) minus 25d call (0.30) = +0.05 -> downside priced richer.
    assert put_skew(iv_by_delta) == pytest.approx(0.05)


def test_put_skew_missing_wing_returns_none():
    assert put_skew({-0.25: 0.35}) is None  # no call wing
    assert put_skew({0.25: 0.30}) is None    # no put wing


def test_implied_downside_breaches_mos():
    # 1-sigma-down = 70. Floor 80 -> breached; floor 60 -> safe.
    breach = implied_downside_breaches_mos(100.0, 0.30, 365, 80.0)
    assert breach["breaches"] is True
    assert breach["sigma_down_price"] == pytest.approx(70.0)
    assert breach["cushion_pct"] == pytest.approx(70.0 / 80.0 - 1.0)

    safe = implied_downside_breaches_mos(100.0, 0.30, 365, 60.0)
    assert safe["breaches"] is False


# --------------------------------------------------------------------------
# 2. Parser + provider
# --------------------------------------------------------------------------
def test_parser_schema_source_and_values():
    vf = _frame()
    # Schema + source tag on every row.
    assert set(["ticker", "metric", "value", "unit", "as_of", "source"]).issubset(vf.df.columns)
    assert (vf.df["source"] == SOURCE_TAG).all()
    assert (vf.df["as_of"] == ASOF).all()

    assert vf.latest("iv_index", "CW") == pytest.approx(0.2850)
    assert vf.latest("iv_rank", "CW") == pytest.approx(0.62)
    assert vf.latest("iv_percentile", "CW") == pytest.approx(0.71)
    assert vf.latest("beta", "CW") == pytest.approx(1.05)
    assert vf.latest("liquidity_rating", "CW") == pytest.approx(3.0)
    assert next_earnings_date(vf, "CW") == "2026-07-30"
    assert len(vf.metric("iv_expiration", ticker="CW")) == 3

    # WES has no earnings and no term structure.
    assert next_earnings_date(vf, "WES") is None
    assert vf.metric("iv_expiration", ticker="WES").empty


def test_volatility_frame_requires_columns():
    import pandas as pd

    with pytest.raises(ValueError):
        VolatilityFrame(pd.DataFrame({"ticker": ["CW"], "metric": ["iv_index"]}))


def test_provider_fetches_parses_caches_and_serves_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)

    calls = {"n": 0}
    payload = _payload()

    def fake_get(self, path, params=None):  # noqa: ARG001
        calls["n"] += 1
        assert path == "/market-metrics"
        assert "symbols" in params
        return payload

    monkeypatch.setattr(TastytradeProvider, "_get", fake_get)

    prov = TastytradeProvider(
        username="u", password="p", base_url="https://x", user_agent="t/0", max_retries=1
    )

    vf = prov.get_volatility(["CW", "WES"])
    assert vf.latest("iv_index", "CW") == pytest.approx(0.2850)
    assert calls["n"] == 1
    assert (tmp_path / "volatility_CW.parquet").exists()
    assert (tmp_path / "volatility_WES.parquet").exists()

    # Second call, same day -> served from cache, no extra HTTP.
    vf2 = prov.get_volatility(["CW", "WES"])
    assert vf2.latest("iv_rank", "CW") == pytest.approx(0.62)
    assert calls["n"] == 1


def test_available_reads_env(monkeypatch):
    monkeypatch.delenv("TASTYTRADE_USERNAME", raising=False)
    monkeypatch.delenv("TASTYTRADE_PASSWORD", raising=False)
    assert TastytradeProvider.available() is False
    monkeypatch.setenv("TASTYTRADE_USERNAME", "u")
    monkeypatch.setenv("TASTYTRADE_PASSWORD", "p")
    assert TastytradeProvider.available() is True


# --------------------------------------------------------------------------
# 3. Overlay assembler + flags
# --------------------------------------------------------------------------
def test_overlay_cw_downside_block_and_flags():
    vf = _frame()
    ov = volatility_overlay(vf, "CW", price=100.0, mos_floor=95.0, asof=ASOF)

    assert ov["iv_rank"] == pytest.approx(62.0)
    assert ov["iv_percentile"] == pytest.approx(71.0)
    assert ov["expected_move_30d_pct"] == pytest.approx(0.2850 * math.sqrt(30 / 365.0))
    assert ov["days_to_earnings"] == 61

    # Earnings expected move uses the first expiration on/after the report
    # date (2026-08-21, iv 0.31) over its days-to-expiry (83 calendar days).
    earn = expected_move_into_earnings(vf, "CW", asof=ASOF)
    assert earn["basis"] == "post_earnings_expiration"
    assert earn["iv"] == pytest.approx(0.31)
    assert earn["days"] == 83
    assert ov["expected_move_earnings_pct"] == pytest.approx(0.31 * math.sqrt(83 / 365.0))

    # IV rank 62 -> elevated (not high); 1-sigma-down (~91.8) < floor 95 -> breach.
    assert "elevated_iv_rank" in ov["vol_flags"]
    assert "high_iv_rank" not in ov["vol_flags"]
    assert "implied_downside_breaches_mos" in ov["vol_flags"]
    assert ov["vol_source"] == "tastytrade"


def test_overlay_mos_not_breached_when_floor_low():
    vf = _frame()
    ov = volatility_overlay(vf, "CW", price=100.0, mos_floor=80.0, asof=ASOF)
    assert "implied_downside_breaches_mos" not in ov["vol_flags"]


def test_overlay_wes_thin_liquidity_low_ivrank():
    vf = _frame()
    ov = volatility_overlay(vf, "WES", asof=ASOF)
    assert ov["iv_rank"] == pytest.approx(18.0)
    assert "elevated_iv_rank" not in ov["vol_flags"]
    assert "thin_liquidity" in ov["vol_flags"]      # liquidity-rating 2
    assert ov["days_to_earnings"] is None           # no earnings date
    assert ov["expected_move_earnings_pct"] is None


# --------------------------------------------------------------------------
# 4. Screener annotation path
# --------------------------------------------------------------------------
def test_enrich_skips_gracefully_without_creds(monkeypatch):
    monkeypatch.setattr(TastytradeProvider, "available", staticmethod(lambda: False))
    row = ScreenerRow(ticker="CW", name="CW", gics_sector=None, is_bank=False, market_cap=None)
    note = _enrich_rows_with_vol([row])
    assert note is not None and "skipped" in note
    assert row.iv_rank is None  # untouched


def test_enrich_annotates_rows_with_injected_provider(monkeypatch):
    vf = _frame()

    class FakeProvider:
        @staticmethod
        def available() -> bool:
            return True

        def get_volatility(self, tickers):  # noqa: ARG002
            return vf

    # _enrich imports TastytradeProvider from the adapters package at call time.
    monkeypatch.setattr("sycamore_prep.adapters.TastytradeProvider", FakeProvider)

    row = ScreenerRow(ticker="CW", name="CW", gics_sector=None, is_bank=False, market_cap=None)
    note = _enrich_rows_with_vol([row])
    assert note is None
    assert row.iv_rank == pytest.approx(62.0)
    assert row.vol_source == "tastytrade"
    assert "elevated_iv_rank" in row.vol_flags

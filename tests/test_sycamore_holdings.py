"""Victory Sycamore holdings loader + universe-builder integration."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.universe import builder as builder_mod
from sycamore_prep.universe.builder import build_universe
from sycamore_prep.universe.sycamore import (
    SYCAMORE_SOURCE,
    load_sycamore_holdings,
    sycamore_holdings_path,
    sycamore_overlay_frame,
)


def test_committed_holdings_file_present():
    assert sycamore_holdings_path().exists(), "data/sycamore_holdings.csv should be committed"


def test_load_holdings_both_funds_cash_dropped():
    h = load_sycamore_holdings()
    counts = h["fund"].value_counts().to_dict()
    assert counts.get("Established Value Fund") == 72
    assert counts.get("Small Company Opportunity Fund") == 104
    assert len(h) == 176
    # Cash / non-equity rows dropped (no blank tickers, no cash sweep name).
    assert (h["ticker"].str.len() > 0).all()
    assert not h["name"].astype(str).str.contains("Cash", case=False).any()
    # Tickers normalized; known names present.
    assert "LH" in set(h["ticker"]) and "UBSI" in set(h["ticker"])
    assert h["ticker"].equals(h["ticker"].str.upper())
    assert pd.api.types.is_numeric_dtype(h["weight_pct"])


def test_overlay_frame_schema():
    f = sycamore_overlay_frame()
    assert list(f.columns) == ["ticker", "name", "gics_sector", "market_cap", "weight_pct", "source"]
    assert (f["source"] == SYCAMORE_SOURCE).all()
    assert f["market_cap"].isna().all()   # position value is NOT company market cap


def test_build_universe_uses_committed_sycamore_when_no_raw(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    cache = tmp_path / "cache"
    raw.mkdir()
    cache.mkdir()
    monkeypatch.setattr(builder_mod, "raw_dir", lambda: raw)
    monkeypatch.setattr(builder_mod, "cache_dir", lambda: cache)
    # No data/raw CSVs -> the committed Sycamore holdings become the overlay.
    df = build_universe()
    by_ticker = df.set_index("ticker") if "ticker" in df.columns else df
    assert "LH" in by_ticker.index and "UBSI" in by_ticker.index
    assert bool(by_ticker.loc["LH", "owned_by_sycamore"]) is True
    assert bool(by_ticker.loc["LH", "in_iws"]) is False
    assert (cache / "universe.parquet").exists()

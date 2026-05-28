"""Universe builder tests. No network; we hand-build holdings CSVs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep.universe import builder


IWS_CSV = """\
iShares Russell Mid-Cap Value ETF
As of date,2025-05-27
Inception Date,2001-07-17

Ticker,Name,Sector,Asset Class,Market Value,Weight (%)
CW,CURTISS-WRIGHT CORP,Industrials,Equity,"1,234,567,890",0.75
WES,WESTERN MIDSTREAM PARTNERS LP,Energy,Equity,"987,654,321",0.50
USD,USD CASH,Cash,Cash,"100,000",0.01
"""

IWN_CSV = """\
iShares Russell 2000 Value ETF
As of date,2025-05-27

Ticker,Name,Sector,Asset Class,Market Value,Weight (%)
UMBF,UMB FINANCIAL CORP,Financials,Equity,"500,000,000",0.40
MTDR,MATADOR RESOURCES CO,Energy,Equity,"450,000,000",0.35
"""

SYCAMORE_CSV = """\
Sycamore Mid-Cap Value Holdings

Ticker,Name,Sector,Market Value,Weight (%)
CW,CURTISS-WRIGHT CORP,Industrials,"1,234,567,890",3.5
LECO,LINCOLN ELECTRIC HOLDINGS,Industrials,"800,000,000",2.8
"""


@pytest.fixture
def raw_dir(tmp_path: Path, monkeypatch) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "iws_holdings.csv").write_text(IWS_CSV)
    (raw / "iwn_holdings.csv").write_text(IWN_CSV)
    (raw / "sycamore_holdings.csv").write_text(SYCAMORE_CSV)

    cache = tmp_path / "cache"
    cache.mkdir()

    # Patch out the project paths so the builder writes into tmp.
    monkeypatch.setattr(builder, "raw_dir", lambda: raw)
    monkeypatch.setattr(builder, "cache_dir", lambda: cache)
    return raw


def test_build_universe_merges_and_flags(raw_dir: Path):
    df = builder.build_universe()
    # 4 unique tickers: CW (IWS+overlay), WES, UMBF, MTDR, LECO -> 5.
    assert set(df["ticker"]) == {"CW", "WES", "UMBF", "MTDR", "LECO"}

    cw = df[df["ticker"] == "CW"].iloc[0]
    assert cw["in_iws"] is True or cw["in_iws"] == 1 or cw["in_iws"] == True  # noqa
    assert cw["owned_by_sycamore"]
    assert "ishares-iws" in cw["source"]
    assert "sycamore-overlay" in cw["source"]

    leco = df[df["ticker"] == "LECO"].iloc[0]
    assert leco["owned_by_sycamore"]
    assert not bool(leco["in_iws"])
    assert not bool(leco["in_iwn"])

    # Cash row is dropped.
    assert "USD" not in set(df["ticker"])


def test_build_universe_skips_missing_files(tmp_path: Path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "iws_holdings.csv").write_text(IWS_CSV)  # only IWS present
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(builder, "raw_dir", lambda: raw)
    monkeypatch.setattr(builder, "cache_dir", lambda: cache)

    df = builder.build_universe()
    assert int(df["in_iws"].sum()) == 2
    assert int(df["in_iwn"].sum()) == 0
    assert int(df["owned_by_sycamore"].sum()) == 0

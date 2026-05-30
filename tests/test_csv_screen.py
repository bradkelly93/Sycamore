"""CsvScreenProvider tests — fully offline, using a temp data/raw CSV."""

from __future__ import annotations

from pathlib import Path

import pytest

from sycamore_prep.adapters import csv_screen as csv_mod
from sycamore_prep.adapters.csv_screen import SOURCE_TAG, CsvScreenProvider
from sycamore_prep.config import load_config


def _csv_name() -> str:
    return load_config().tradingview.csv_file


def test_presence_means_pass_and_carries_signal(tmp_path: Path, monkeypatch):
    (tmp_path / _csv_name()).write_text(
        "ticker,regime\nNASDAQ:AAA,Bullish\nNYSE:MOG.A,Moderate Bear\n"
    )
    monkeypatch.setattr(csv_mod, "raw_dir", lambda: tmp_path)

    out = CsvScreenProvider().get_screen()
    assert list(out["ticker"]) == ["AAA", "MOG.A"]          # exchange stripped, dot kept
    assert out["passes_screen"].all()                       # presence == passing
    assert list(out["regime"]) == ["Bullish", "Moderate Bear"]  # carried verbatim
    assert (out["source"] == SOURCE_TAG).all()
    assert out["asof"].notna().all()


def test_explicit_pass_column_and_numeric_coercion(tmp_path: Path, monkeypatch):
    (tmp_path / _csv_name()).write_text(
        "Symbol,passes_screen,score\nAAA,true,1.5\nBBB,no,0.3\n"
    )
    monkeypatch.setattr(csv_mod, "raw_dir", lambda: tmp_path)

    out = CsvScreenProvider().get_screen().set_index("ticker")
    assert bool(out.loc["AAA", "passes_screen"]) is True
    assert bool(out.loc["BBB", "passes_screen"]) is False
    assert out.loc["AAA", "score"] == 1.5                   # coerced to float
    assert out.loc["BBB", "score"] == 0.3


def test_missing_file_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(csv_mod, "raw_dir", lambda: tmp_path)   # empty dir
    with pytest.raises(FileNotFoundError):
        CsvScreenProvider().get_screen()


def test_missing_ticker_column_raises(tmp_path: Path, monkeypatch):
    (tmp_path / _csv_name()).write_text("foo,bar\n1,2\n")
    monkeypatch.setattr(csv_mod, "raw_dir", lambda: tmp_path)
    with pytest.raises(ValueError):
        CsvScreenProvider().get_screen()

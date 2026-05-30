"""Polymarket adapter tests — exercise parsing + the read-only HTTP surface
against canned Gamma payloads. No network (monkeypatched `_get`)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.base import MARKET_COLUMNS
from sycamore_prep.adapters.polymarket import PolymarketProvider, _as_list


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _market() -> dict:
    return json.loads((FIXTURE_DIR / "polymarket_market.json").read_text())


def test_as_list_decodes_stringified_arrays_and_passes_native():
    # Gamma encodes these as JSON STRINGS, not native arrays.
    assert _as_list('["Yes", "No"]') == ["Yes", "No"]
    assert _as_list(["A", "B"]) == ["A", "B"]
    assert _as_list(None) == []
    assert _as_list("not json") == []


def test_parse_market_yields_one_row_per_outcome_with_probs():
    prov = PolymarketProvider()
    rows = prov._parse_market(_market(), as_of="2026-05-30T00:00:00Z")
    df = pd.DataFrame(rows)
    assert len(df) == 2  # Yes + No
    yes = df[df["outcome"] == "Yes"].iloc[0]
    # outcomePrices[i] IS the implied probability of outcomes[i].
    assert yes["implied_prob"] == 0.32
    assert df[df["outcome"] == "No"].iloc[0]["implied_prob"] == 0.68
    assert yes["source"] == "polymarket (non-primary)"
    assert yes["resolution_date"] == "2026-12-31"
    assert yes["volume"] == 1875000.0
    assert yes["liquidity"] == 42000.0
    assert yes["slug"] == "spirit-airlines-bankruptcy-2026"
    assert "polymarket.com" in yes["url"]


def test_search_markets_parses_public_search(monkeypatch):
    prov = PolymarketProvider()
    data = json.loads((FIXTURE_DIR / "polymarket_search.json").read_text())
    monkeypatch.setattr(prov, "_get", lambda url, params=None: data)
    df = prov.search_markets("Spirit Airlines")
    assert list(df.columns) == MARKET_COLUMNS
    assert (df["source"] == "polymarket (non-primary)").all()
    assert "Spirit Airlines" in df.iloc[0]["question"]


def test_get_market_returns_outcomes_and_caches_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    prov = PolymarketProvider()
    monkeypatch.setattr(prov, "_get", lambda url, params=None: [_market()])
    df = prov.get_market("spirit-airlines-bankruptcy-2026")
    assert list(df.columns) == MARKET_COLUMNS
    assert len(df) == 2
    # Snapshot cached keyed by slug + as_of date so the overlay can diff it.
    snaps = cache_mod.list_market_snapshots("spirit-airlines-bankruptcy-2026")
    assert len(snaps) == 1

"""Polymarket adapter tests — exercise parsing + the read-only HTTP surface
against canned Gamma payloads. No network (monkeypatched `_get`)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.base import MARKET_COLUMNS
from sycamore_prep.adapters.polymarket import (
    PolymarketProvider,
    _as_list,
    _category_for,
    _coerce_market_objects,
    _extract_markets_from_search,
)


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


def test_get_market_uses_path_style_slug_endpoint(tmp_path, monkeypatch):
    """Migrated off the deprecated `/markets?slug=` query form to the
    path-style `/markets/slug/{slug}`, which returns a single market OBJECT."""
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    prov = PolymarketProvider()
    seen: dict = {}

    def fake_get(url, params=None):
        seen["url"] = url
        seen["params"] = params
        return _market()  # single object, not a list — the new shape

    monkeypatch.setattr(prov, "_get", fake_get)
    df = prov.get_market("spirit-airlines-bankruptcy-2026")
    assert seen["url"].endswith("/markets/slug/spirit-airlines-bankruptcy-2026")
    assert seen["params"] is None  # slug is in the path, not a query param
    assert len(df) == 2  # single object coerced + parsed into 2 outcome rows


def test_coerce_market_objects_handles_object_list_and_wrappers():
    obj = {"slug": "x"}
    assert _coerce_market_objects(obj) == [obj]              # single object
    assert _coerce_market_objects([obj]) == [obj]            # list
    assert _coerce_market_objects({"markets": [obj]}) == [obj]
    assert _coerce_market_objects({"data": [obj]}) == [obj]
    assert _coerce_market_objects(None) == []


def test_search_folds_event_tags_into_category():
    """Event-level tags (where Polymarket actually marks Crypto/Sports) get
    folded into each market's `category` so discovery can filter on them."""
    data = {
        "events": [{
            "slug": "btc-updown",
            "title": "Bitcoin Up or Down",
            "tags": [{"label": "Crypto"}, {"label": "Bitcoin"}, "Up or Down"],
            "markets": [{"slug": "btc-updown-4h", "question": "Bitcoin Up or Down - ET",
                         "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]'}],
        }],
    }
    markets = _extract_markets_from_search(data)
    assert len(markets) == 1
    cat = markets[0]["category"]
    assert "Crypto" in cat and "Bitcoin" in cat


def test_category_for_reads_nested_event_tags():
    market = {"slug": "m", "category": None,
              "events": [{"tags": [{"label": "Tennis"}, {"label": "Sports"}]}]}
    assert _category_for(market) == "Tennis, Sports"

"""Overlay tests: confirmed/macro filtering, downside-first ordering, the
read-through classifier, the positioning (prob-change) diff from cached
snapshots, and the screener annotation collapse. No network (FakeProvider)."""

from __future__ import annotations

import pandas as pd
import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.base import EventProbabilityProvider, MARKET_COLUMNS
from sycamore_prep.prediction_markets.overlay import (
    build_overlay,
    screener_annotations,
    write_overlay,
)


def _mkt_rows(slug: str, question: str, yes_prob: float) -> list[dict]:
    common = dict(
        slug=slug, question=question, volume=10000.0, liquidity=2000.0,
        resolution_date="2026-12-31", category=None,
        url=f"https://polymarket.com/event/{slug}",
        as_of="2026-05-30T00:00:00Z", source="polymarket (non-primary)",
    )
    return [
        {**common, "outcome": "Yes", "implied_prob": yes_prob},
        {**common, "outcome": "No", "implied_prob": round(1 - yes_prob, 2)},
    ]


class _FakeProvider(EventProbabilityProvider):
    def __init__(self, markets: dict[str, list[dict]]):
        self._markets = markets

    def search_markets(self, query, *, active_only=True, limit=50):
        return pd.DataFrame(columns=MARKET_COLUMNS)

    def get_market(self, slug):
        return pd.DataFrame(self._markets.get(slug, []), columns=MARKET_COLUMNS)


@pytest.fixture
def mapping_csv(tmp_path):
    df = pd.DataFrame([
        {"ticker": "SAVE", "aperture": "company", "slug": "save-ch11",
         "question": "Will Spirit file Ch11?", "event_type": "distress",
         "direction": "risk", "relevance_score": 0.7, "confirmed": True, "notes": ""},
        {"ticker": "CW", "aperture": "industry", "slug": "defense-budget",
         "question": "Will the defense budget rise in 2026?", "event_type": "macro",
         "direction": "opportunity", "relevance_score": 0.6, "confirmed": True, "notes": ""},
        {"ticker": "XYZ", "aperture": "macro", "slug": "us-recession",
         "question": "US recession in 2026?", "event_type": "macro",
         "direction": "risk", "relevance_score": 0.9, "confirmed": True, "notes": ""},
        {"ticker": "SAVE", "aperture": "company", "slug": "save-merger",
         "question": "Will Spirit be acquired?", "event_type": "m&a",
         "direction": "opportunity", "relevance_score": 0.5, "confirmed": False, "notes": ""},
    ])
    p = tmp_path / "prediction_markets.csv"
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def provider():
    return _FakeProvider({
        "save-ch11": _mkt_rows("save-ch11", "Will Spirit file Ch11?", 0.30),
        "defense-budget": _mkt_rows("defense-budget", "Will the defense budget rise in 2026?", 0.55),
        "us-recession": _mkt_rows("us-recession", "US recession in 2026?", 0.40),
        "save-merger": _mkt_rows("save-merger", "Will Spirit be acquired?", 0.25),
    })


def test_build_overlay_filters_confirmed_and_excludes_macro_by_default(
    tmp_path, monkeypatch, mapping_csv, provider
):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    df = build_overlay(provider, mapping_path=mapping_csv,
                       confirmed_only=True, include_macro=False)
    slugs = set(df["slug"])
    assert "save-ch11" in slugs            # confirmed company
    assert "defense-budget" in slugs       # confirmed industry
    assert "us-recession" not in slugs     # macro aperture excluded by default
    assert "save-merger" not in slugs      # unconfirmed excluded
    # Downside-first: the RISK read-through sorts to the very top.
    assert df.iloc[0]["slug"] == "save-ch11"
    assert df.iloc[0]["read_through"] == "RISK"


def test_include_macro_and_downside_only(tmp_path, monkeypatch, mapping_csv, provider):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    df = build_overlay(provider, mapping_path=mapping_csv, confirmed_only=True,
                       include_macro=True, downside_only=True)
    assert set(df["read_through"]) == {"RISK"}
    assert set(df["slug"]) == {"save-ch11", "us-recession"}


def test_read_through_classifier(tmp_path, monkeypatch, mapping_csv):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    # Opportunity at high prob → OPPORTUNITY; risk below contradiction_prob → WATCH.
    prov = _FakeProvider({
        "save-ch11": _mkt_rows("save-ch11", "Will Spirit file Ch11?", 0.05),  # below 0.20
        "defense-budget": _mkt_rows("defense-budget", "Will the defense budget rise?", 0.55),
    })
    df = build_overlay(prov, mapping_path=mapping_csv, confirmed_only=True)
    rt = df.set_index("slug")["read_through"].to_dict()
    assert rt["save-ch11"] == "WATCH"           # risk but only 5% → not material
    assert rt["defense-budget"] == "OPPORTUNITY"


def test_prob_change_from_cached_snapshot(tmp_path, monkeypatch, mapping_csv):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    # Seed a ~30-day-old snapshot at 0.20; current is 0.30 → +0.10 move.
    old = pd.DataFrame(_mkt_rows("save-ch11", "Will Spirit file Ch11?", 0.20))
    cache_mod.save_market_snapshot("save-ch11", "2026-04-30", old)
    prov = _FakeProvider({"save-ch11": _mkt_rows("save-ch11", "Will Spirit file Ch11?", 0.30)})
    df = build_overlay(prov, tickers=["SAVE"], mapping_path=mapping_csv, confirmed_only=True)
    row = df[df["slug"] == "save-ch11"].iloc[0]
    assert row["prob_chg_30d"] == pytest.approx(0.10, abs=1e-6)


def test_screener_annotations_one_row_per_ticker(tmp_path, monkeypatch, mapping_csv, provider):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    overlay = build_overlay(provider, mapping_path=mapping_csv, confirmed_only=True)
    ann = screener_annotations(overlay)
    assert "SAVE" in ann.index
    assert ann.loc["SAVE", "event_read_through"] == "RISK"
    assert ann.loc["SAVE", "event_top_prob"] == pytest.approx(0.30)


def test_write_overlay_creates_xlsx_and_csv(tmp_path, monkeypatch, mapping_csv, provider):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    df = build_overlay(provider, tickers=["SAVE"], mapping_path=mapping_csv, confirmed_only=True)
    out = tmp_path / "overlay.xlsx"
    write_overlay(df, out)
    assert out.exists()
    assert out.with_suffix(".csv").exists()


def test_empty_when_nothing_confirmed(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    empty = tmp_path / "empty.csv"
    pd.DataFrame(columns=[
        "ticker", "aperture", "slug", "question", "event_type",
        "direction", "relevance_score", "confirmed", "notes",
    ]).to_csv(empty, index=False)
    df = build_overlay(provider, mapping_path=empty, confirmed_only=True)
    assert df.empty


# --------------------------------------------------------------------------- #
# Time-to-resolution filter: near-dated markets are nearly decided, so their
# implied prob is backward-looking — drop them (and surface days_to_resolution).
# --------------------------------------------------------------------------- #

from sycamore_prep.prediction_markets.discover import days_to_resolution, _passes_horizon  # noqa: E402


def test_days_to_resolution_and_horizon_helpers():
    assert days_to_resolution("2026-12-31", as_of="2026-06-01") == 213
    assert days_to_resolution(None) is None
    assert days_to_resolution("not-a-date") is None
    # Far-out market passes; near-dated fails; unknown date always passes.
    assert _passes_horizon("2026-12-31", 30, as_of="2026-06-01") is True
    assert _passes_horizon("2026-06-10", 30, as_of="2026-06-01") is False
    assert _passes_horizon(None, 30, as_of="2026-06-01") is True
    assert _passes_horizon("2026-06-10", 0, as_of="2026-06-01") is True   # filter disabled


def _near_far_provider():
    # near: resolves 5 days after as_of (2026-05-30) -> dropped by the 30d filter.
    near = _mkt_rows("fed-imminent", "Fed cut at the next meeting?", 0.02)
    for r in near:
        r["resolution_date"] = "2026-06-04"
    far = _mkt_rows("recession", "US recession by end 2026?", 0.35)  # 2026-12-31, kept
    return _FakeProvider({"fed-imminent": near, "recession": far})


def test_build_overlay_drops_near_dated_markets(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)
    mp = tmp_path / "prediction_markets.csv"
    pd.DataFrame([
        {"ticker": "UMBF", "aperture": "industry", "slug": "fed-imminent",
         "question": "Fed cut at the next meeting?", "event_type": "macro",
         "direction": "risk", "relevance_score": 0.7, "confirmed": True, "notes": ""},
        {"ticker": "UMBF", "aperture": "industry", "slug": "recession",
         "question": "US recession by end 2026?", "event_type": "macro",
         "direction": "risk", "relevance_score": 0.7, "confirmed": True, "notes": ""},
    ]).to_csv(mp, index=False)

    df = build_overlay(_near_far_provider(), mapping_path=mp, confirmed_only=True)
    slugs = set(df["slug"])
    assert "recession" in slugs            # far-dated kept
    assert "fed-imminent" not in slugs     # near-dated (5d) dropped
    # The surfaced market carries a days_to_resolution column.
    assert "days_to_resolution" in df.columns
    assert int(df[df["slug"] == "recession"]["days_to_resolution"].iloc[0]) > 30

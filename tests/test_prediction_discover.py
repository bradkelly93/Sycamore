"""Discovery + mapping tests: relevance scoring, event-type guessing, the
company aperture, and the human-preserving upsert. No network."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.adapters.base import EventProbabilityProvider, MARKET_COLUMNS
from sycamore_prep.config import load_config
from sycamore_prep.prediction_markets.discover import (
    discover_for_ticker,
    guess_event_type_direction,
    relevance_score,
)
from sycamore_prep.prediction_markets.mapping import upsert


def _mkt(slug: str, question: str, prob: float = 0.3) -> dict:
    return {
        "slug": slug, "question": question, "outcome": "Yes", "implied_prob": prob,
        "volume": 1000.0, "liquidity": 500.0, "resolution_date": "2026-12-31",
        "category": None, "url": f"https://polymarket.com/event/{slug}",
        "as_of": "2026-05-30T00:00:00Z", "source": "polymarket (non-primary)",
    }


class _FakeProvider(EventProbabilityProvider):
    def __init__(self, by_query: dict[str, list[dict]]):
        self._by_query = by_query

    def search_markets(self, query, *, active_only=True, limit=50):
        rows = self._by_query.get(query, [])
        return pd.DataFrame(rows, columns=MARKET_COLUMNS)

    def get_market(self, slug):
        return pd.DataFrame(columns=MARKET_COLUMNS)


def test_relevance_scores_company_match_above_unrelated():
    q_match = "Will Spirit Airlines file for Chapter 11 bankruptcy in 2026?"
    q_unrelated = "Will Bitcoin close above $100,000 in 2026?"
    s_match = relevance_score("Spirit Airlines", q_match, "SAVE")
    s_unrelated = relevance_score("Spirit Airlines", q_unrelated, "SAVE")
    assert s_match > 0.5
    assert s_unrelated < 0.3
    assert s_match > s_unrelated


def test_ticker_hit_boosts_score():
    q = "Will CW win the new Navy propulsion contract?"
    assert relevance_score("Curtiss Wright", q, "CW") > relevance_score("Curtiss Wright", q, "ZZZ")


def test_guess_event_type_direction():
    assert guess_event_type_direction("Will Acme file Chapter 11?") == ("distress", "risk")
    assert guess_event_type_direction("Will Beta be acquired by Gamma?") == ("m&a", "opportunity")
    assert guess_event_type_direction("Will the FDA approve drug X?") == ("regulatory", "opportunity")
    assert guess_event_type_direction("Will it rain tomorrow?") == ("other", "neutral")


def test_discover_for_ticker_company_aperture():
    cfg = load_config()
    prov = _FakeProvider({
        "Spirit Airlines": [_mkt("save-ch11-2026",
                                 "Will Spirit Airlines file Chapter 11 in 2026?")],
    })
    df = discover_for_ticker(
        prov, "SAVE", "Spirit Airlines", None, cfg,
        apertures=["company"], min_relevance=0.3,
    )
    assert not df.empty
    row = df.iloc[0]
    assert row["aperture"] == "company"
    assert row["slug"] == "save-ch11-2026"
    assert row["event_type"] == "distress"
    assert row["direction"] == "risk"
    assert row["relevance_score"] >= 0.3


def test_discover_filters_below_min_relevance():
    cfg = load_config()
    prov = _FakeProvider({
        "Spirit Airlines": [_mkt("btc", "Will Bitcoin close above $100,000 in 2026?")],
    })
    df = discover_for_ticker(
        prov, "SAVE", "Spirit Airlines", None, cfg,
        apertures=["company"], min_relevance=0.5,
    )
    assert df.empty  # unrelated market scored below threshold


def test_upsert_preserves_human_edits_and_adds_new_candidates():
    existing = pd.DataFrame([{
        "ticker": "SAVE", "aperture": "company", "slug": "save-ch11-2026",
        "question": "old text", "event_type": "distress", "direction": "risk",
        "relevance_score": 0.6, "confirmed": True, "notes": "watch closely",
    }])
    candidates = pd.DataFrame([
        {  # same market re-discovered — refresh machine fields only
            "ticker": "SAVE", "aperture": "company", "slug": "save-ch11-2026",
            "question": "new text", "event_type": "distress", "direction": "risk",
            "relevance_score": 0.82,
        },
        {  # brand-new candidate — must land as unconfirmed
            "ticker": "SAVE", "aperture": "peer", "slug": "ual-merger-2026",
            "question": "Will United Airlines merge in 2026?", "event_type": "m&a",
            "direction": "opportunity", "relevance_score": 0.5,
        },
    ])
    merged = upsert(existing, candidates)
    assert len(merged) == 2

    save = merged[merged["slug"] == "save-ch11-2026"].iloc[0]
    assert bool(save["confirmed"]) is True          # human flag preserved
    assert save["notes"] == "watch closely"         # human note preserved
    assert save["relevance_score"] == 0.82          # machine field refreshed
    assert save["question"] == "new text"           # machine field refreshed

    new = merged[merged["slug"] == "ual-merger-2026"].iloc[0]
    assert bool(new["confirmed"]) is False          # new candidate unconfirmed

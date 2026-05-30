"""Discovery + mapping tests: relevance scoring, event-type guessing, the
company aperture, and the human-preserving upsert. No network."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.adapters.base import EventProbabilityProvider, MARKET_COLUMNS
from sycamore_prep.config import load_config
from sycamore_prep.prediction_markets.discover import (
    _is_noise_category,
    discover_for_ticker,
    guess_event_type_direction,
    relevance_score,
)
from sycamore_prep.prediction_markets.mapping import upsert


def _mkt(slug: str, question: str, prob: float = 0.3, category: object = None) -> dict:
    return {
        "slug": slug, "question": question, "outcome": "Yes", "implied_prob": prob,
        "volume": 1000.0, "liquidity": 500.0, "resolution_date": "2026-12-31",
        "category": category, "url": f"https://polymarket.com/event/{slug}",
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


def _cfg_with_peers(**peers: list[str]):
    """Deep-copy the shared config and set `peers` on the copy — never mutate
    the lru_cached singleton (it leaks into other test modules)."""
    cfg = load_config().model_copy(deep=True)
    cfg.peers.clear()
    cfg.peers.update(peers)
    return cfg


def test_relevance_scores_company_match_above_unrelated():
    q_match = "Will Spirit Airlines file for Chapter 11 bankruptcy in 2026?"
    q_unrelated = "Will Bitcoin close above $100,000 in 2026?"
    s_match = relevance_score("Spirit Airlines", q_match, "SAVE")
    s_unrelated = relevance_score("Spirit Airlines", q_unrelated, "SAVE")
    assert s_match > 0.5
    assert s_unrelated < 0.3
    assert s_match > s_unrelated


def test_ticker_hit_only_for_long_ticker_or_cashtag():
    # A bare 2–3 letter ticker must NOT earn the bonus — it collides with
    # ordinary words ("ET", "PR", "ASB") and caused the peer false positives.
    q_bare = "Will CW win the new Navy propulsion contract?"
    assert relevance_score("Curtiss Wright", q_bare, "CW") == relevance_score(
        "Curtiss Wright", q_bare, "ZZ"
    )
    # A 4+ char ticker as a standalone token still earns the bonus.
    q_long = "Will MTDR beat production guidance in Q3?"
    assert relevance_score("Matador Resources", q_long, "MTDR") > relevance_score(
        "Matador Resources", q_long, None
    )
    # A cashtag earns the bonus at any length.
    q_cash = "Will $CW raise its dividend?"
    assert relevance_score("Curtiss Wright", q_cash, "CW") > relevance_score(
        "Curtiss Wright", "Will CW raise its dividend?", "CW"
    )


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


def test_is_noise_category_token_matched():
    # Crypto / sports / pop-culture tag strings are noise...
    assert _is_noise_category("Crypto, Bitcoin, Up or Down")
    assert _is_noise_category("Sports, Soccer, Fifa Friendly")
    assert _is_noise_category("Tennis, Sports, Games")
    assert _is_noise_category("Celebrities, Music, Culture")
    assert _is_noise_category("Esports, Dota 2")
    # ...but economy/policy tags (the recession market) are NOT, and token
    # matching means a noise token can't hit as a substring of a real word
    # ('mma' is inside 'summary', 'nba' inside 'urbana').
    assert not _is_noise_category("Economic Policy, Business, Economy")
    assert not _is_noise_category("Quarterly summary, Urbana outlook")
    assert not _is_noise_category(None)


def test_peer_false_positives_dropped_by_name_search_and_category_filter():
    """The exact bug: peers ET/PR/ASB matched crypto/sports markets via their
    bare tickers. Post-fix the peer aperture searches by COMPANY NAME and drops
    crypto/sports categories — so none of these survive."""
    # Provider keyed by what the FIXED code searches: resolved company NAMES.
    prov = _FakeProvider({
        "Energy Transfer LP": [
            _mkt("btc-updown-4h-1780171200", "Bitcoin Up or Down - May 30, ET",
                 category="Crypto, Bitcoin, Up or Down"),
            _mkt("eth-updown-5m-1780187100", "Ethereum Up or Down - May 30, ET",
                 category="Crypto, Ethereum, Up or Down"),
        ],
        "Permian Resources Corp": [
            _mkt("fif-sin-chn-2026-06-05-chn", "Will China PR win on 2026-06-05?",
                 category="Sports, Games, Soccer, Fifa Friendly"),
        ],
        "Associated Banc-Corp": [
            _mkt("wta-ku-charaev-2026-01-02",
                 "ASB Classic, Qualification: Yeon-Woo Ku vs Alina Charaeva",
                 category="Tennis, Sports, Games"),
        ],
    })
    resolver = {"ET": "Energy Transfer LP", "PR": "Permian Resources Corp",
                "ASB": "Associated Banc-Corp"}.get

    for tk, peers in (("WES", ["ET"]), ("MTDR", ["PR"]), ("UMBF", ["ASB"])):
        cfg = _cfg_with_peers(**{tk: peers})
        df = discover_for_ticker(
            prov, tk, tk, None, cfg,
            apertures=["peer"], min_relevance=cfg.polymarket.min_relevance,
            peer_name_resolver=resolver,
        )
        assert df.empty, f"{tk}: expected zero peer matches, got {df['slug'].tolist()}"


def test_peer_searched_by_resolved_name_not_ticker():
    """A legitimate peer market is found by company name; the search is issued
    under the NAME, never the bare ticker."""
    cfg = _cfg_with_peers(WES=["EPD"])
    prov = _FakeProvider({
        "Enterprise Products Partners": [
            _mkt("epd-distribution-cut-2026",
                 "Will Enterprise Products Partners cut its distribution in 2026?",
                 category="Business"),
        ],
        # If the code wrongly searched the bare ticker, it'd hit this junk.
        "EPD": [_mkt("et-timezone-junk", "Anything at 5pm ET", category="Crypto")],
    })
    resolver = {"EPD": "Enterprise Products Partners"}.get
    df = discover_for_ticker(
        prov, "WES", "WES", None, cfg,
        apertures=["peer"], min_relevance=0.3, peer_name_resolver=resolver,
    )
    assert list(df["slug"]) == ["epd-distribution-cut-2026"]
    assert df.iloc[0]["aperture"] == "peer"


def test_peer_falls_back_to_ticker_when_name_unresolvable():
    """When name resolution fails (SEC down / ticker absent), fall back to the
    raw ticker so the aperture still functions."""
    cfg = _cfg_with_peers(MTDR=["CIVI"])
    prov = _FakeProvider({
        "CIVI": [_mkt("civi-2026", "Will CIVI acquire a Permian operator in 2026?",
                      category="Business")],
    })
    def resolver(_ticker):  # resolution always fails
        return None

    df = discover_for_ticker(
        prov, "MTDR", "MTDR", None, cfg,
        apertures=["peer"], min_relevance=0.3, peer_name_resolver=resolver,
    )
    assert list(df["slug"]) == ["civi-2026"]


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

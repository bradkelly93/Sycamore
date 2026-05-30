"""Discovery: propose ticker→market matches with a transparent relevance score.

`relevance_score` (0–1) is an auditable blend, NOT a black box (CLAUDE.md):

    0.50 * token_overlap(name, question)   # fraction of name tokens in the Q
    0.30 * sequence_ratio(name, question)  # fuzzy string similarity (stdlib)
    0.20 * ticker_hit                      # 1 if the ticker is a standalone
                                           #   token in the question, else 0

The aim isn't precision — it's giving the analyst a ranked, editable starting
point in the mapping CSV. The human confirms/edits from there.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
from functools import lru_cache

import pandas as pd

from ..adapters.base import EventProbabilityProvider
from ..config import AppConfig


# Corporate-form / filler tokens that shouldn't drive a match.
_STOP = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "holdings", "holding", "group", "the", "and", "class",
    "common", "stock", "lp", "trust", "industries", "international",
}
_WORD = re.compile(r"[A-Za-z0-9']+")

# Category/tag tokens that mark a market as off-thesis noise for a bottom-up
# equity pitch (crypto, sports/esports, pop-culture). Polymarket exposes this
# as event-level `tags` (e.g. ['Crypto','Bitcoin'], ['Sports','Soccer']); the
# adapter folds those into the `category` column. Applied to the company + peer
# apertures only — the industry/macro apertures keep everything (a recession
# market is tagged 'Economy'/'Business' and must survive). Token-matched (not
# substring) so 'mma' can't hit inside 'summary'.
_NOISE_CATEGORY_TOKENS = {
    "crypto", "bitcoin", "ethereum", "solana", "dogecoin", "xrp", "bnb",
    "sports", "esports", "soccer", "tennis", "basketball", "baseball",
    "hockey", "football", "nba", "nfl", "mlb", "nhl", "ufc", "mma", "golf",
    "celebrities", "celebrity", "music", "culture", "entertainment", "movies",
}


def _is_noise_category(category: object) -> bool:
    """True if a market's category/tags mark it crypto/sports/pop-culture."""
    if not category:
        return False
    toks = {w.lower() for w in _WORD.findall(str(category))}
    return bool(toks & _NOISE_CATEGORY_TOKENS)


def _tokens(text: str) -> list[str]:
    return [w for w in (t.lower() for t in _WORD.findall(text or "")) if w not in _STOP]


def _token_overlap(name: str, question: str) -> float:
    nt = set(_tokens(name))
    if not nt:
        return 0.0
    return len(nt & set(_tokens(question))) / len(nt)


def _seq_ratio(name: str, question: str) -> float:
    return SequenceMatcher(None, (name or "").lower(), (question or "").lower()).ratio()


def _ticker_hit(ticker: str | None, question: str) -> float:
    """Award the ticker bonus only when the match is unambiguous.

    A bare 2–3 letter ticker collides with ordinary words and abbreviations
    ("ET" = Eastern Time, "PR" = People's Republic, "ASB" = ASB Classic) — the
    root cause of the peer-aperture false positives — so a bare short token is
    NOT credited. The bonus fires only for a cashtag ($TICKER, any length) or a
    standalone token of a 4+ character ticker.
    """
    if not ticker:
        return 0.0
    t = ticker.strip()
    if not t:
        return 0.0
    q = question or ""
    if re.search(rf"\${re.escape(t)}\b", q, flags=re.IGNORECASE):
        return 1.0
    if len(t) >= 4 and t.lower() in _tokens(q):
        return 1.0
    return 0.0


def relevance_score(name: str, question: str, ticker: str | None = None) -> float:
    score = (
        0.50 * _token_overlap(name, question)
        + 0.30 * _seq_ratio(name, question)
        + 0.20 * _ticker_hit(ticker, question)
    )
    return round(min(score, 1.0), 3)


# Keyword → (event_type, default direction). Direction is a *starting guess*
# the analyst overrides in the CSV; downside-leaning events default to "risk".
_EVENT_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("bankrupt", "chapter 11", "default", "insolven", "delist", "restructur"),
     "distress", "risk"),
    (("acquire", "acquisition", "merger", "merge", "buyout", "take private", "tender"),
     "m&a", "opportunity"),
    (("fda", "approval", "approve", "phase 3", "phase iii", "clinical trial"),
     "regulatory", "opportunity"),
    (("lawsuit", "settle", "litigation", "fine", "antitrust", "investigation", "probe"),
     "litigation", "risk"),
    (("spin off", "spinoff", "spin-off", "split off", "carve out"),
     "spinoff", "opportunity"),
    (("recession", "rate cut", "rate hike", "inflation", "gdp", "shutdown",
      "tariff", "oil price", "crude", "copper", "gold price"),
     "macro", "neutral"),
    (("ceo", "resign", "guidance", "earnings", "beat", "miss", "layoff"),
     "company", "neutral"),
]


def guess_event_type_direction(question: str) -> tuple[str, str]:
    q = (question or "").lower()
    for keywords, event_type, direction in _EVENT_RULES:
        if any(k in q for k in keywords):
            return event_type, direction
    return "other", "neutral"


# Apertures whose candidates are filtered for crypto/sports/pop-culture noise.
# A peer is identified by a short ticker that collides with everyday words, so
# its market list is the dirtiest; the company aperture gets the same hygiene.
# industry/macro are keyword-driven and intentionally keep economy/policy tags.
_NOISE_FILTERED_APERTURES = {"company", "peer"}


@lru_cache(maxsize=1)
def _edgar_name_resolver() -> Callable[[str], str | None]:
    """Default peer-ticker → company-name resolver, backed by the EDGAR
    ticker→name map. Built lazily and cached so a single SEC ticker-map pull
    serves every peer across every name in a run. Returns a function that maps
    a ticker to its company name, or None if SEC is unreachable / the ticker is
    absent — callers fall back to the raw ticker in that case."""
    from ..adapters.edgar import EdgarProvider  # local import avoids a cycle

    try:
        provider = EdgarProvider()
        name_map = {
            t: rec.get("name") or None
            for t, rec in provider._ticker_to_cik_map().items()
        }
    except Exception:  # noqa: BLE001 — SEC egress blocked / map unavailable
        name_map = {}

    def resolve(ticker: str) -> str | None:
        t = (ticker or "").upper()
        return name_map.get(t) or name_map.get(t.replace(".", "-"))

    return resolve


def discover_for_ticker(
    provider: EventProbabilityProvider,
    ticker: str,
    name: str,
    sector: str | None,
    cfg: AppConfig,
    *,
    apertures: list[str],
    min_relevance: float,
    peer_name_resolver: Callable[[str], str | None] | None = None,
) -> pd.DataFrame:
    """Search each requested aperture and return candidate mapping rows
    (ticker, aperture, slug, question, event_type, direction, relevance_score)
    above `min_relevance`. Reuses the configured `peers` map (peer aperture)
    and `prediction.sector_keywords` / `macro_markets` (industry/macro).

    Peers are searched and scored by COMPANY NAME (resolved via
    `peer_name_resolver`, default EDGAR), not by their short ticker — a 2–3
    letter ticker collides with ordinary words ("ET", "PR", "ASB") and floods
    the peer aperture with timezone/sports/abbreviation false positives. The
    raw ticker is used only when name resolution fails. The company + peer
    apertures additionally drop crypto/sports/pop-culture markets."""
    resolve_peer = peer_name_resolver or _edgar_name_resolver()
    candidates: list[dict] = []

    def collect(markets: pd.DataFrame, aperture: str, match_name: str,
                match_ticker: str | None = None) -> None:
        if markets is None or markets.empty:
            return
        drop_noise = aperture in _NOISE_FILTERED_APERTURES
        has_category = "category" in markets.columns
        for slug, grp in markets.groupby("slug"):
            row0 = grp.iloc[0]
            if drop_noise and has_category and _is_noise_category(row0.get("category")):
                continue
            question = str(row0["question"])
            rel = relevance_score(match_name, question, match_ticker)
            if rel < min_relevance:
                continue
            event_type, direction = guess_event_type_direction(question)
            candidates.append({
                "ticker": ticker, "aperture": aperture, "slug": slug,
                "question": question, "event_type": event_type,
                "direction": direction, "relevance_score": rel,
            })

    if "company" in apertures:
        collect(provider.search_markets(name), "company", name, ticker)
    if "peer" in apertures:
        for peer in cfg.peers.get(ticker, []):
            peer_name = resolve_peer(peer)
            # Search/score by name when resolvable; fall back to the ticker.
            query = peer_name or peer
            # Only credit a ticker_hit for the fallback (raw-ticker) path; when
            # we matched by name the ticker plays no role in scoring.
            collect(provider.search_markets(query), "peer", query,
                    None if peer_name else peer)
    if "industry" in apertures and sector:
        for keyword in cfg.prediction.sector_keywords.get(sector, []):
            collect(provider.search_markets(keyword), "industry", keyword)
    if "macro" in apertures:
        for spec in cfg.prediction.macro_markets:
            if "*" in spec.applies_to or (sector and sector in spec.applies_to):
                collect(provider.search_markets(spec.query), "macro", spec.query)

    if not candidates:
        from .mapping import MAPPING_COLUMNS  # local import avoids a cycle
        return pd.DataFrame(columns=[c for c in MAPPING_COLUMNS
                                     if c not in ("confirmed", "notes")])
    return pd.DataFrame(candidates)

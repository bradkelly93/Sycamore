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
from difflib import SequenceMatcher

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
    if not ticker:
        return 0.0
    return 1.0 if ticker.lower() in _tokens(question) else 0.0


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


def discover_for_ticker(
    provider: EventProbabilityProvider,
    ticker: str,
    name: str,
    sector: str | None,
    cfg: AppConfig,
    *,
    apertures: list[str],
    min_relevance: float,
) -> pd.DataFrame:
    """Search each requested aperture and return candidate mapping rows
    (ticker, aperture, slug, question, event_type, direction, relevance_score)
    above `min_relevance`. Reuses the configured `peers` map (peer aperture)
    and `prediction.sector_keywords` / `macro_markets` (industry/macro)."""
    candidates: list[dict] = []

    def collect(markets: pd.DataFrame, aperture: str, match_name: str,
                match_ticker: str | None = None) -> None:
        if markets is None or markets.empty:
            return
        for slug, grp in markets.groupby("slug"):
            question = str(grp.iloc[0]["question"])
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
            collect(provider.search_markets(peer), "peer", peer, peer)
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

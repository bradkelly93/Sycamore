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
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache

import pandas as pd

from ..adapters.base import EventProbabilityProvider
from ..config import AppConfig, DEFAULT_NOISE_CATEGORY_TOKENS


def days_to_resolution(resolution_date: object, as_of: object = None) -> int | None:
    """Calendar days from `as_of` (default today) to a market's resolution date.
    None when the date is missing/unparsable — callers keep such markets (can't
    judge time-to-resolution, so don't drop on it)."""
    if resolution_date is None:
        return None
    try:
        res = pd.Timestamp(str(resolution_date)[:10])
    except (ValueError, TypeError):
        return None
    if pd.isna(res):
        return None
    base = pd.Timestamp(str(as_of)[:10]) if as_of else pd.Timestamp(date.today())
    return int((res.normalize() - base.normalize()).days)


def _passes_horizon(resolution_date: object, min_days: int, as_of: object = None) -> bool:
    """True if the market resolves far enough out to carry forward-looking
    signal (or its date is unknown). A near-dated market is nearly decided."""
    if not min_days:
        return True
    d = days_to_resolution(resolution_date, as_of)
    return d is None or d >= min_days


# Corporate-form / filler tokens that shouldn't drive a match.
_STOP = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "holdings", "holding", "group", "the", "and", "class",
    "common", "stock", "lp", "trust", "industries", "international",
}
_WORD = re.compile(r"[A-Za-z0-9']+")


def _is_noise_category(category: object, noise_tokens: object = None) -> bool:
    """True if a market's category/tags mark it crypto/sports/pop-culture.

    `noise_tokens` is the configurable set from
    `config.prediction.noise_category_tokens` (falls back to the canonical
    default when None). Token-matched, not substring — so 'mma' can't hit
    inside 'summary' nor 'nba' inside 'urbana'.
    """
    if not category:
        return False
    tokens = DEFAULT_NOISE_CATEGORY_TOKENS if noise_tokens is None else noise_tokens
    cat_toks = {w.lower() for w in _WORD.findall(str(category))}
    return bool(cat_toks & {str(t).lower() for t in tokens})


def _row_volume(row: object) -> float:
    """Traded volume for a market row, used only as a cap tie-breaker. Missing
    or non-numeric volume sorts last (0.0)."""
    try:
        v = float(row.get("volume"))  # type: ignore[union-attr]
    except (TypeError, ValueError, AttributeError):
        return 0.0
    return v if v == v else 0.0  # NaN-guard


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
    noise_tokens = {str(t).lower() for t in cfg.prediction.noise_category_tokens}
    cap = cfg.prediction.max_markets_per_query
    min_days = cfg.prediction.min_days_to_resolution
    candidates: list[dict] = []

    def collect(markets: pd.DataFrame, aperture: str, match_name: str,
                match_ticker: str | None = None) -> None:
        if markets is None or markets.empty:
            return
        drop_noise = aperture in _NOISE_FILTERED_APERTURES
        has_category = "category" in markets.columns
        has_res = "resolution_date" in markets.columns
        hits: list[dict] = []
        for slug, grp in markets.groupby("slug"):
            row0 = grp.iloc[0]
            if (drop_noise and has_category
                    and _is_noise_category(row0.get("category"), noise_tokens)):
                continue
            # Drop near-dated markets — a market resolving within `min_days` is
            # nearly decided, so its implied prob is backward-looking, not a live
            # risk read. Markets with no parsable date are kept.
            if has_res and not _passes_horizon(row0.get("resolution_date"), min_days):
                continue
            question = str(row0["question"])
            rel = relevance_score(match_name, question, match_ticker)
            if rel < min_relevance:
                continue
            event_type, direction = guess_event_type_direction(question)
            hits.append({
                "ticker": ticker, "aperture": aperture, "slug": slug,
                "question": question, "event_type": event_type,
                "direction": direction, "relevance_score": rel,
                "_volume": _row_volume(row0),
            })
        # Cap each query's hits to the top-N — threshold-laddered markets
        # (Bitcoin/WTI price strikes) otherwise return dozens of near-identical
        # rows. Rank by relevance, tie-broken by traded volume (depth).
        if cap and len(hits) > cap:
            hits.sort(key=lambda h: (h["relevance_score"], h["_volume"]), reverse=True)
            hits = hits[:cap]
        for h in hits:
            h.pop("_volume", None)
            candidates.append(h)

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

"""Polymarket adapter. NON-PRIMARY — implied probabilities, not fundamentals.

Read-only market data only: discovery, metadata, and current implied
probabilities. This adapter never authenticates, signs, or places orders —
prediction-market *trading* is an explicit non-goal (see CLAUDE.md). Every row
carries `source="polymarket (non-primary)"`.

Endpoints (verify against current docs before relying on changes — Polymarket
ships breaking changes without notice). Both Gamma and the CLOB read endpoints
are fully public (no API key):

    Gamma  https://gamma-api.polymarket.com/markets         (metadata + prices)
    Gamma  https://gamma-api.polymarket.com/public-search   (keyword search)
    CLOB   https://clob.polymarket.com/midpoint             (fresher mid, opt.)

A market's `outcomePrices` array maps 1:1 to `outcomes` and IS the implied
probability vector: outcomePrices[i] ≈ P(outcomes[i]). A 0.62 "Yes" price means
the market implies a 62% chance of Yes.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from ..config import load_config
from . import cache
from .base import MARKET_COLUMNS, EventProbabilityProvider


SOURCE_TAG = "polymarket (non-primary)"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_list(raw: Any) -> list[Any]:
    """Gamma encodes `outcomes`/`outcomePrices`/`clobTokenIds` as JSON STRINGS
    (e.g. '["Yes", "No"]'), not native arrays. Decode defensively."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_date(v: Any) -> str | None:
    if not v:
        return None
    try:
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _extract_markets_from_search(data: Any) -> list[dict[str, Any]]:
    """Gamma public-search returns {events: [{markets: [...]}], ...}. Flatten
    to a market list, tolerating either a flat array, a {markets:[...]} object,
    or nested events."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("markets"), list):
        return data["markets"]
    out: list[dict[str, Any]] = []
    for ev in data.get("events", []) or []:
        if not isinstance(ev, dict):
            continue
        for m in ev.get("markets", []) or []:
            # Carry the event's slug/title down if the market lacks them.
            m.setdefault("slug", ev.get("slug"))
            m.setdefault("question", ev.get("title"))
            out.append(m)
    return out


class PolymarketProvider(EventProbabilityProvider):
    name = "polymarket"

    def __init__(
        self,
        gamma_base_url: str | None = None,
        clob_base_url: str | None = None,
        rate_limit_rps: float | None = None,
        user_agent: str | None = None,
    ):
        cfg = load_config().polymarket
        self._gamma = (gamma_base_url or cfg.gamma_base_url).rstrip("/")
        self._clob = (clob_base_url or cfg.clob_base_url).rstrip("/")
        self._rps = rate_limit_rps or cfg.rate_limit_rps
        self._ua = user_agent or cfg.user_agent
        self._last_call: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": self._ua, "Accept": "application/json"}
        )

    # ---------- HTTP ----------

    def _throttle(self) -> None:
        min_interval = 1.0 / float(self._rps)
        delta = time.monotonic() - self._last_call
        if delta < min_interval:
            time.sleep(min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self._throttle()
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ---------- Parsing ----------

    def _parse_market(self, obj: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
        """Flatten one Gamma market object into one MARKET_COLUMNS row per
        outcome. outcomePrices[i] is the implied probability of outcomes[i]."""
        outcomes = _as_list(obj.get("outcomes"))
        prices = _as_list(obj.get("outcomePrices"))
        slug = obj.get("slug") or str(obj.get("id", ""))
        question = obj.get("question") or obj.get("title") or ""
        # Prefer the *Num variants (native floats) over the string fields.
        volume = _to_float(obj.get("volumeNum"))
        if volume is None:
            volume = _to_float(obj.get("volume"))
        liquidity = _to_float(obj.get("liquidityNum"))
        if liquidity is None:
            liquidity = _to_float(obj.get("liquidity"))
        resolution = _iso_date(obj.get("endDate") or obj.get("end_date"))
        category = obj.get("category") or obj.get("groupItemTitle") or None
        url = f"https://polymarket.com/event/{slug}" if slug else ""

        common = {
            "slug": slug, "question": question, "volume": volume,
            "liquidity": liquidity, "resolution_date": resolution,
            "category": category, "url": url, "as_of": as_of,
            "source": SOURCE_TAG,
        }
        rows: list[dict[str, Any]] = []
        for i, outcome in enumerate(outcomes):
            prob = _to_float(prices[i]) if i < len(prices) else None
            rows.append({**common, "outcome": str(outcome), "implied_prob": prob})
        if not rows:  # market with no decodable outcomes still gets a stub row
            rows.append({**common, "outcome": None, "implied_prob": None})
        return rows

    def _frame(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=MARKET_COLUMNS)
        return pd.DataFrame(rows)[MARKET_COLUMNS]

    # ---------- Public (read-only) ----------

    def search_markets(
        self, query: str, *, active_only: bool = True, limit: int = 50
    ) -> pd.DataFrame:
        """Keyword-search via Gamma `public-search`. Returns MARKET_COLUMNS.

        The exact search endpoint/params are the one piece most likely to drift
        across Polymarket releases — they're isolated here so a fix never
        touches downstream code (Swappable-data-layer principle). Falls back to
        filtering the `/markets` listing if public-search is unavailable.
        """
        as_of = _utc_now_iso()
        params: dict[str, Any] = {"q": query, "limit_per_type": limit}
        if active_only:
            params["events_status"] = "active"
        try:
            data = self._get(f"{self._gamma}/public-search", params=params)
        except Exception:  # noqa: BLE001 — fall back to the markets listing
            return self._search_via_markets(query, active_only, limit, as_of)

        rows: list[dict[str, Any]] = []
        for m in _extract_markets_from_search(data)[:limit]:
            rows.extend(self._parse_market(m, as_of))
        return self._frame(rows)

    def _search_via_markets(
        self, query: str, active_only: bool, limit: int, as_of: str
    ) -> pd.DataFrame:
        """Fallback: page the /markets listing and filter by question text."""
        params: dict[str, Any] = {
            "limit": min(limit * 5, 500), "order": "volumeNum", "ascending": "false",
        }
        if active_only:
            params["closed"] = "false"
            params["active"] = "true"
        data = self._get(f"{self._gamma}/markets", params=params)
        markets = data if isinstance(data, list) else data.get("data", [])
        q = query.lower()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in markets:
            text = f"{m.get('question', '')} {m.get('description', '')}".lower()
            if q in text:
                rows.extend(self._parse_market(m, as_of))
                seen.add(m.get("slug") or "")
            if len(seen) >= limit:
                break
        return self._frame(rows)

    def get_market(self, slug: str) -> pd.DataFrame:
        """Current snapshot for one market slug; caches it keyed by slug+date."""
        as_of = _utc_now_iso()
        data = self._get(f"{self._gamma}/markets", params={"slug": slug})
        markets = data if isinstance(data, list) else data.get("data", [])
        rows: list[dict[str, Any]] = []
        for m in markets:
            rows.extend(self._parse_market(m, as_of))
        frame = self._frame(rows)
        if not frame.empty:
            cache.save_market_snapshot(slug, as_of[:10], frame)
        return frame

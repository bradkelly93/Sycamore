"""tastytrade adapter — options/volatility market data for the downside overlay.

VOLATILITY OVERLAY, NOT A TRADING TOOL. This adapter only READS market-level
volatility metrics — IV rank/percentile, the IV index, beta, option liquidity,
and the per-expiration IV term structure — for tickers under research. It never
reads account positions and never places orders. Your tastytrade account is
used purely as an authenticated gateway to the `market-metrics` feed.

Credentials come from the ENVIRONMENT, never config.yaml (which is committed):

    export TASTYTRADE_USERNAME=...
    export TASTYTRADE_PASSWORD=...

Per CLAUDE.md primary-source discipline: tastytrade is a legitimate primary
source for *options/vol* data (it is the venue), so rows are tagged
source="tastytrade". A vol metric must never be spliced into a fundamentals
metric without both sources labeled.

Resilience mirrors the yfinance adapter: this is convenience/overlay data, so
callers wrap pulls and degrade gracefully when creds or network are absent.

Field names below match tastytrade's documented `market-metrics` response.
Parsing is defensive (every field via `.get`, missing -> skipped), so minor
schema drift leaves a metric blank rather than crashing. Verify the live shape
on your first authenticated run.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Iterable

import pandas as pd

from ..config import load_config
from . import cache
from .base import VOLATILITY_COLUMNS, VolatilityFrame, VolatilityProvider


SOURCE_TAG = "tastytrade"

# tastytrade market-metrics JSON field -> (canonical metric, unit). Anything
# not present in the live payload is simply skipped.
_SCALAR_FIELD_MAP = {
    "implied-volatility-index": ("iv_index", "ratio"),
    "implied-volatility-index-5-day-change": ("iv_index_5d_change", "ratio"),
    "implied-volatility-index-rank": ("iv_rank", "rank"),
    "implied-volatility-percentile": ("iv_percentile", "rank"),
    "beta": ("beta", "beta"),
    "liquidity-rating": ("liquidity_rating", "score"),
    "liquidity-rank": ("liquidity_rank", "rank"),
}


class TastytradeError(RuntimeError):
    """Auth / transport failure talking to tastytrade."""


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row(ticker, metric, value, unit, as_of, detail=None) -> dict:
    return {
        "ticker": ticker.upper(),
        "metric": metric,
        "value": value,
        "unit": unit,
        "as_of": as_of,
        "detail": detail,
        "source": SOURCE_TAG,
    }


def _empty_vol_df() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in VOLATILITY_COLUMNS})


def parse_market_metrics(payload: dict, as_of: str) -> pd.DataFrame:
    """Pure: tastytrade /market-metrics JSON -> tidy long VolatilityFrame rows.

    Isolated from transport so it can be unit-tested against a fixture, the
    same pattern the EdgarProvider parser uses.
    """
    items = ((payload or {}).get("data") or {}).get("items") or []
    rows: list[dict] = []
    for item in items:
        symbol = (item.get("symbol") or "").upper()
        if not symbol:
            continue
        for field, (metric, unit) in _SCALAR_FIELD_MAP.items():
            val = _to_float(item.get(field))
            if val is not None:
                rows.append(_row(symbol, metric, val, unit, as_of))
        earnings = item.get("earnings") or {}
        edate = earnings.get("expected-report-date")
        if edate:
            rows.append(_row(symbol, "next_earnings", float("nan"), "date", as_of, detail=str(edate)))
        for exp in item.get("option-expiration-implied-volatilities") or []:
            iv = _to_float(exp.get("implied-volatility"))
            exp_date = exp.get("expiration-date")
            if iv is not None and exp_date:
                rows.append(_row(symbol, "iv_expiration", iv, "ratio", as_of, detail=str(exp_date)))
    if not rows:
        return _empty_vol_df()
    return pd.DataFrame(rows)


class TastytradeProvider(VolatilityProvider):
    name = "tastytrade"

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        user_agent: str | None = None,
        max_retries: int = 3,
        session=None,
    ):
        tt = getattr(load_config(), "tastytrade", None)
        self.base_url = (base_url or (tt.base_url if tt else None) or "https://api.tastytrade.com").rstrip("/")
        self.user_agent = user_agent or (tt.user_agent if tt else None) or "sycamore-prep/0.1"
        self._username = username or os.environ.get("TASTYTRADE_USERNAME")
        self._password = password or os.environ.get("TASTYTRADE_PASSWORD")
        self._max_retries = max(1, max_retries)
        self._session = session
        self._token: str | None = None

    @staticmethod
    def available() -> bool:
        """True if credentials are present — lets callers skip vol cleanly."""
        return bool(os.environ.get("TASTYTRADE_USERNAME") and os.environ.get("TASTYTRADE_PASSWORD"))

    # ---- HTTP (isolated so tests can monkeypatch _get / _post) ----
    def _client(self):
        if self._session is None:
            import requests  # lazy: keep import out of test paths that patch _get
            self._session = requests.Session()
        return self._session

    def _headers(self, auth: bool) -> dict:
        h = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if auth:
            h["Authorization"] = self._require_token()
        return h

    def _post(self, path: str, json: dict) -> dict:
        resp = self._client().post(
            f"{self.base_url}{path}", json=json, headers=self._headers(auth=False), timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client().get(
            f"{self.base_url}{path}", params=params, headers=self._headers(auth=True), timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    # ---- auth ----
    def _login(self) -> str:
        if not self._username or not self._password:
            raise TastytradeError(
                "tastytrade credentials missing. Set TASTYTRADE_USERNAME and "
                "TASTYTRADE_PASSWORD in your environment (never config.yaml)."
            )
        payload = self._post("/sessions", {"login": self._username, "password": self._password})
        token = ((payload or {}).get("data") or {}).get("session-token")
        if not token:
            raise TastytradeError("tastytrade login returned no session-token.")
        return token

    def _require_token(self) -> str:
        if self._token is None:
            self._token = self._login()
        return self._token

    # ---- public ----
    def get_volatility(self, tickers: Iterable[str], use_cache: bool = True) -> VolatilityFrame:
        """Batched pull of today's vol metrics for `tickers`.

        market-metrics accepts many symbols per call, so we fetch only the
        tickers not already cached for today, in a single request.
        """
        wanted = [t.upper() for t in tickers]
        as_of = date.today().isoformat()
        frames: list[pd.DataFrame] = []
        to_fetch: list[str] = []
        for t in wanted:
            cached = cache.load_volatility(t) if use_cache else None
            if cached is not None and "as_of" in cached.columns and (cached["as_of"] == as_of).any():
                frames.append(cached[cached["as_of"] == as_of])
            else:
                to_fetch.append(t)

        if to_fetch:
            payload = self._get_with_retry("/market-metrics", {"symbols": ",".join(to_fetch)})
            fetched = parse_market_metrics(payload, as_of=as_of)
            for t, sub in fetched.groupby("ticker"):
                cache.save_volatility(str(t), sub)
                frames.append(sub)

        if not frames:
            return VolatilityFrame(_empty_vol_df())
        return VolatilityFrame(pd.concat(frames, ignore_index=True))

    def _get_with_retry(self, path: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self._get(path, params)
            except Exception as exc:  # transport is flaky; broad except is intentional
                last = exc
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
        raise TastytradeError(
            f"market-metrics fetch failed after {self._max_retries} retries"
        ) from last

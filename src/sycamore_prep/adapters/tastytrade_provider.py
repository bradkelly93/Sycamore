"""tastytrade adapter — options/volatility market data for the downside overlay.

VOLATILITY OVERLAY, NOT A TRADING TOOL. This adapter only READS market-level
volatility metrics — IV rank/percentile, the IV index, beta, option liquidity,
and the per-expiration IV term structure — for tickers under research. It never
reads account positions and never places orders. Your tastytrade account is
used purely as an authenticated gateway to the `market-metrics` feed.

AUTH (OAuth2). tastytrade discontinued username/password session-tokens on
2025-12-01, so this uses OAuth2. One-time setup in your tastytrade account:
  1. "OAuth Applications" -> create an app -> save the CLIENT SECRET.
  2. "Manage" -> "Create Grant" -> save the REFRESH TOKEN (it never expires).
Then put them in the ENVIRONMENT (never config.yaml, which is committed):

    export TASTYTRADE_CLIENT_SECRET=...
    export TASTYTRADE_REFRESH_TOKEN=...

(The tastytrade SDK's TT_SECRET / TT_REFRESH names are also accepted.) At call
time we exchange the refresh token for a ~15-minute Bearer access token via
POST /oauth/token, refreshing as needed.

PRIMARY-SOURCE DISCIPLINE (CLAUDE.md). tastytrade is a legitimate primary
source for *options/vol* data (it is the venue), so rows are tagged
source="tastytrade". We intentionally pull only vol-domain fields — market cap,
P/E, EPS and dividends are NOT taken from tastytrade, so fundamentals stay
EDGAR-sourced. A vol metric must never be spliced into a fundamentals metric
without both sources labeled.

Field names are verified against the tastytrade SDK v12 `MarketMetricInfo`
model (2025-11 API). Parsing stays defensive (every field via `.get`, missing
-> skipped) so future schema drift leaves a metric blank rather than crashing.
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

# tastytrade market-metrics JSON field -> (canonical metric, unit). Verified
# against MarketMetricInfo; anything absent from the live payload is skipped.
# NOTE: rank/percentile arrive as strings in the API — `_to_float` coerces.
_SCALAR_FIELD_MAP = {
    "implied-volatility-index": ("iv_index", "ratio"),
    "implied-volatility-index-5-day-change": ("iv_index_5d_change", "ratio"),
    "implied-volatility-index-rank": ("iv_rank", "rank"),
    "implied-volatility-percentile": ("iv_percentile", "rank"),
    "implied-volatility-30-day": ("iv_30_day", "ratio"),
    "historical-volatility-30-day": ("hv_30_day", "ratio"),
    "iv-hv-30-day-difference": ("iv_hv_30_day_diff", "ratio"),
    "beta": ("beta", "beta"),
    "corr-spy-3month": ("corr_spy_3m", "corr"),
    "liquidity-rating": ("liquidity_rating", "score"),
    "liquidity-rank": ("liquidity_rank", "rank"),
}


class TastytradeError(RuntimeError):
    """Auth / transport failure talking to tastytrade."""


def _to_float(v) -> float | None:
    if v is None or v == "":
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

    Accepts the full response envelope ({"data": {"items": [...]}}). Isolated
    from transport so it can be unit-tested against a fixture, the same pattern
    the EdgarProvider parser uses.
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
        client_secret: str | None = None,
        refresh_token: str | None = None,
        base_url: str | None = None,
        user_agent: str | None = None,
        api_version: str | None = None,
        max_retries: int = 3,
        session=None,
    ):
        tt = getattr(load_config(), "tastytrade", None)
        self.base_url = (base_url or (tt.base_url if tt else None) or "https://api.tastyworks.com").rstrip("/")
        self.user_agent = user_agent or (tt.user_agent if tt else None) or "sycamore-prep/0.1"
        self.api_version = api_version if api_version is not None else (tt.api_version if tt else "20251101")
        self._client_secret = (
            client_secret or os.environ.get("TASTYTRADE_CLIENT_SECRET") or os.environ.get("TT_SECRET")
        )
        self._refresh_token = (
            refresh_token or os.environ.get("TASTYTRADE_REFRESH_TOKEN") or os.environ.get("TT_REFRESH")
        )
        self._max_retries = max(1, max_retries)
        self._session = session
        self._token: str | None = None
        self._token_expiry = 0.0

    @staticmethod
    def available() -> bool:
        """True if OAuth credentials are present — lets callers skip vol cleanly."""
        secret = os.environ.get("TASTYTRADE_CLIENT_SECRET") or os.environ.get("TT_SECRET")
        refresh = os.environ.get("TASTYTRADE_REFRESH_TOKEN") or os.environ.get("TT_REFRESH")
        return bool(secret and refresh)

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
        if self.api_version:
            h["Accept-Version"] = self.api_version
        if auth:
            h["Authorization"] = f"Bearer {self._require_token()}"
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

    # ---- auth (OAuth2 refresh-token grant) ----
    def _login(self) -> str:
        if not self._client_secret or not self._refresh_token:
            raise TastytradeError(
                "tastytrade OAuth credentials missing. Set TASTYTRADE_CLIENT_SECRET "
                "and TASTYTRADE_REFRESH_TOKEN in your environment (never config.yaml). "
                "Create them under 'OAuth Applications' in your tastytrade account."
            )
        payload = self._post(
            "/oauth/token",
            {
                "grant_type": "refresh_token",
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            },
        )
        token = (payload or {}).get("access_token")
        if not token:
            raise TastytradeError("tastytrade OAuth returned no access_token.")
        # Access tokens last ~15 min; keep a 60s safety buffer before expiry.
        self._token_expiry = time.time() + float(payload.get("expires_in", 900)) - 60.0
        return token

    def _require_token(self) -> str:
        if self._token is None or time.time() >= self._token_expiry:
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

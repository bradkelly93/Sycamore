"""TradingView technical-screen adapter. NON-PRIMARY — every row carries a
`source` tag, and the data is a CONTEXT overlay only.

Per CLAUDE.md this toolkit is bottom-up and downside-first: a technical signal
must never enter the three-attribute fundamental score. This provider replicates
the analyst's OWN saved TradingView screen — expressed declaratively in
`config.yaml` so it stays auditable — against TradingView's scanner endpoint via
the (unofficial) `tradingview-screener` library, and returns screen membership
plus any carried indicator columns. The screener overlays this beside the
fundamental output, never inside the score.

Caveats: the endpoint is unofficial and can change; realtime fields require the
user's own session cookie (anonymous access returns delayed data, which is the
safe default for this EOD-flavored overlay).
"""

from __future__ import annotations

import os
import sys
import time

import pandas as pd

from . import cache
from .base import TechnicalScreenProvider
from ..config import load_config


SOURCE_TAG = "tradingview (non-primary, technical)"

# Columns the scanner may return that are identifiers, not technical indicators.
_IDENTIFIER_FIELDS = {"name", "description", "ticker", "exchange", "type", "subtype"}


def _tv():
    # Lazy import so offline tests and non-overlay runs never need the library.
    from tradingview_screener import Query, col
    return Query, col


def _normalize_symbol(raw: str) -> str:
    """`NASDAQ:AAPL` -> `AAPL`; `NYSE:MOG.A` -> `MOG.A`.

    Keep the class-share dot — TradingView and this repo's own ticker
    convention (config.yaml peers list `MOG.A`) agree, so a bare strip + upper
    lines up the join key. A non-match just yields `passes_screen=False`.
    """
    s = str(raw)
    if ":" in s:
        s = s.split(":")[-1]
    return s.strip().upper()


def _compile_filters(filters: list[dict], col) -> list:
    """Compile declarative `{field, op, value}` dicts into `tradingview_screener`
    conditions (AND-combined by `Query.where`). For the field-vs-field ops
    (`above`/`below`/`crosses_*`) `value` is ANOTHER FIELD NAME; otherwise it is
    a literal. Raises ValueError on an unknown op so config typos surface.
    """
    conds = []
    for f in filters:
        field = f["field"]
        op = f["op"]
        val = f.get("value")
        c = col(field)
        if op == "gt":
            conds.append(c > val)
        elif op == "lt":
            conds.append(c < val)
        elif op == "ge":
            conds.append(c >= val)
        elif op == "le":
            conds.append(c <= val)
        elif op == "eq":
            conds.append(c == val)
        elif op == "between":
            conds.append(c.between(val[0], val[1]))
        elif op == "isin":
            conds.append(c.isin(val))
        elif op == "above":
            conds.append(c > col(val))
        elif op == "below":
            conds.append(c < col(val))
        elif op == "crosses_above":
            conds.append(c.crosses_above(col(val)))
        elif op == "crosses_below":
            conds.append(c.crosses_below(col(val)))
        else:
            raise ValueError(
                f"Unknown TradingView filter op {op!r} (field={field!r}). "
                "Supported: gt,lt,ge,le,eq,between,isin,above,below,"
                "crosses_above,crosses_below."
            )
    return conds


class TradingViewProvider(TechnicalScreenProvider):
    name = "tradingview"

    def __init__(self, max_retries: int = 3):
        self._max_retries = max_retries

    def _retry(self, fn, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # unofficial endpoint is flaky; broad except intentional
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(
            f"tradingview-screener call failed after {self._max_retries} retries"
        ) from last

    def get_screen(self, tickers=None, refresh: bool = False) -> pd.DataFrame:  # noqa: ARG002 — full-market screen; candidate list ignored
        """Pull the user's saved screen once and return a tidy membership frame.

        Single scanner call (the full passing set for the region) + a high limit;
        the screener resolves per-ticker membership by a local join. Cached to a
        single parquet with a TTL so repeat runs replay offline.
        """
        cfg = load_config().tradingview
        if not refresh:
            cached = cache.load_tv_screen(cfg.cache_ttl_minutes)
            if cached is not None:
                return cached

        Query, col = _tv()
        query = (
            Query()
            .set_markets(cfg.region)
            .select(*cfg.select)
            .where(*_compile_filters(cfg.filters, col))
            .limit(cfg.max_results)
        )
        sessionid = cfg.sessionid or os.environ.get("TV_SESSIONID")
        cookies = {"sessionid": sessionid} if sessionid else None
        count, df = self._retry(lambda: query.get_scanner_data(cookies=cookies))

        df = df.copy()
        if count is not None and count > len(df):
            df.attrs["tv_truncated"] = True
            print(
                f"[tv-overlay] WARNING: screen matched {count} names but only "
                f"{len(df)} returned (limit={cfg.max_results}); membership may be "
                "truncated. Raise tradingview.max_results.",
                file=sys.stderr,
            )

        if "ticker" in df.columns:
            df["ticker"] = df["ticker"].map(_normalize_symbol)
        else:
            df["ticker"] = pd.Series([], dtype="object")
        df["passes_screen"] = True
        df["asof"] = pd.Timestamp.now(tz="UTC").isoformat()
        df["source"] = SOURCE_TAG

        cache.save_tv_screen(df)
        return df

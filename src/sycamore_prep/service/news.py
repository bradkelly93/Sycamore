"""Recent-headlines service — a NON-PRIMARY, read-only context panel.

Wraps ``YFinanceProvider.get_news`` into a :class:`NewsView`. Like the other
overlays it is walled off from every scoring/valuation path (CLAUDE.md): it never
feeds the screener, comps, or fair value. Degrades to a clear note when offline or
when the feed returns nothing — never a traceback.
"""

from __future__ import annotations

from datetime import datetime

from ..adapters import YFinanceProvider
from .serde import clean_scalar
from .viewmodels import NewsItem, NewsView

SOURCE = "yfinance (non-primary)"


def _fmt_published(v) -> str | None:
    v = clean_scalar(v)
    if v is None:
        return None
    # yfinance gives either an epoch int or an ISO string depending on version.
    try:
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(float(v)).strftime("%Y-%m-%d")
        s = str(v)
        return s[:10] if len(s) >= 10 else s
    except (ValueError, OSError):
        return str(v)


def for_ticker(ticker: str, *, limit: int = 8) -> NewsView:
    ticker = ticker.upper()
    try:
        df = YFinanceProvider().get_news(ticker, limit=limit)
    except Exception as exc:  # noqa: BLE001 — best-effort context, never fatal
        return NewsView(ticker=ticker, note=f"Couldn't fetch headlines: {exc}")
    if df is None or df.empty:
        return NewsView(ticker=ticker, note="No recent headlines found (or news feed "
                        "unavailable — needs internet access).")
    items = [
        NewsItem(
            title=str(clean_scalar(r["title"]) or ""),
            publisher=str(clean_scalar(r["publisher"]) or ""),
            link=str(clean_scalar(r["link"]) or ""),
            published=_fmt_published(r["published"]),
        )
        for _, r in df.iterrows() if clean_scalar(r["title"])
    ]
    return NewsView(ticker=ticker, items=items,
                    note=None if items else "No recent headlines found.")

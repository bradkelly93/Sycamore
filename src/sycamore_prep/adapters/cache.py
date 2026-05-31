"""Parquet cache keyed by ticker + concept + period. Provider-agnostic."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import cache_dir


def _path(ticker: str) -> Path:
    return cache_dir() / f"financials_{ticker.upper()}.parquet"


def load_financials(ticker: str) -> pd.DataFrame | None:
    p = _path(ticker)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save_financials(ticker: str, df: pd.DataFrame) -> Path:
    p = _path(ticker)
    df.to_parquet(p, index=False)
    return p


def has_financials(ticker: str) -> bool:
    return _path(ticker).exists()


def sector_path(ticker: str) -> Path:
    return cache_dir() / f"sector_{ticker.upper()}.parquet"


def load_sector(ticker: str) -> str | None:
    """Cached GICS sector for a ticker, or None if not cached. Only successful
    lookups are cached (see save_sector), so a miss means 'never resolved' —
    distinct from a resolved-but-empty result."""
    p = sector_path(ticker)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:  # noqa: BLE001 — corrupt/partial cache file; treat as miss
        return None
    if df.empty or "sector" not in df.columns:
        return None
    val = df.iloc[0]["sector"]
    return str(val) if val is not None and str(val) != "" else None


def save_sector(ticker: str, sector: str, source: str = "") -> Path:
    """Cache a successfully-resolved GICS sector. Carries `source` + `as_of`
    tags for auditability. Callers must NOT cache failed/empty lookups, so a
    transient outage retries next run instead of sticking a permanent miss."""
    p = sector_path(ticker)
    pd.DataFrame([{
        "ticker": ticker.upper(),
        "sector": sector,
        "source": source,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }]).to_parquet(p, index=False)
    return p


def prices_path(ticker: str) -> Path:
    return cache_dir() / f"prices_{ticker.upper()}.parquet"


def load_prices(ticker: str) -> pd.DataFrame | None:
    p = prices_path(ticker)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save_prices(ticker: str, df: pd.DataFrame) -> Path:
    p = prices_path(ticker)
    df.to_parquet(p, index=False)
    return p


# --------------------------------------------------------------------------- #
# Prediction-market snapshots
#
# Implied probabilities move, so a snapshot is keyed by `slug + as_of_date`
# (not just slug) — this keeps runs reproducible/offline-replayable AND lets
# the overlay compute a probability change ("positioning move") by diffing the
# current snapshot against an older one.
# --------------------------------------------------------------------------- #

def _slug_key(slug: str) -> str:
    """Filesystem-safe key from a market slug."""
    return re.sub(r"[^A-Za-z0-9.-]", "_", str(slug))[:120]


def market_snapshot_path(slug: str, as_of_date: str) -> Path:
    return cache_dir() / f"event_{_slug_key(slug)}_{as_of_date}.parquet"


def save_market_snapshot(slug: str, as_of_date: str, df: pd.DataFrame) -> Path:
    p = market_snapshot_path(slug, as_of_date)
    df.to_parquet(p, index=False)
    return p


def load_market_snapshot(slug: str, as_of_date: str) -> pd.DataFrame | None:
    p = market_snapshot_path(slug, as_of_date)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def list_market_snapshots(slug: str) -> list[tuple[str, Path]]:
    """All cached snapshots for a slug as (as_of_date, path), oldest first.
    The date is the trailing `_YYYY-MM-DD` segment of the filename."""
    key = _slug_key(slug)
    out: list[tuple[str, Path]] = []
    for p in sorted(cache_dir().glob(f"event_{key}_*.parquet")):
        out.append((p.stem.rsplit("_", 1)[-1], p))
    return out

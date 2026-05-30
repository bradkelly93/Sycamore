"""Parquet cache keyed by ticker + concept + period. Provider-agnostic."""

from __future__ import annotations

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


# --- TradingView technical-screen overlay (non-primary, time-sensitive) ---
# A single whole-screen snapshot, not ticker-keyed: it is one point-in-time pull
# of the user's saved screen. Cached with an `asof` timestamp + TTL because
# technicals go stale fast (unlike the rarely-changing primary fundamentals).

def tv_screen_path() -> Path:
    return cache_dir() / "tv_screen.parquet"


def save_tv_screen(df: pd.DataFrame) -> Path:
    p = tv_screen_path()
    df.to_parquet(p, index=False)
    return p


def load_tv_screen(ttl_minutes: int) -> pd.DataFrame | None:
    """Return the cached screen only if it is younger than `ttl_minutes`."""
    p = tv_screen_path()
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if df.empty or "asof" not in df.columns:
        return None
    try:
        asof = pd.Timestamp(df["asof"].iloc[0])
    except Exception:  # noqa: BLE001
        return None
    if asof.tzinfo is None:
        asof = asof.tz_localize("UTC")
    age_min = (pd.Timestamp.now(tz="UTC") - asof).total_seconds() / 60.0
    if age_min > ttl_minutes:
        return None
    return df

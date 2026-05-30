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


def volatility_path(ticker: str) -> Path:
    return cache_dir() / f"volatility_{ticker.upper()}.parquet"


def load_volatility(ticker: str) -> pd.DataFrame | None:
    p = volatility_path(ticker)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save_volatility(ticker: str, df: pd.DataFrame) -> Path:
    """Append today's snapshot, replacing any same-`as_of` rows for the ticker.

    Keeps a rolling history of daily vol observations (vol is time-sensitive,
    unlike a fiscal period) without duplicating a re-pull on the same day.
    """
    p = volatility_path(ticker)
    if p.exists() and not df.empty and "as_of" in df.columns:
        prev = pd.read_parquet(p)
        if "as_of" in prev.columns:
            prev = prev[~prev["as_of"].isin(df["as_of"].unique())]
            df = pd.concat([prev, df], ignore_index=True)
    df.to_parquet(p, index=False)
    return p

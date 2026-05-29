"""Parquet cache keyed by ticker + concept + period. Provider-agnostic.

Tidy tabular pulls are cached as parquet; raw nested API payloads (EDGAR
submissions JSON, full-text-search hits) are cached as JSON sidecars so they
can be replayed offline and audited.
"""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- #
# Generic JSON sidecar (raw nested payloads) + parquet (tidy frames)
# --------------------------------------------------------------------------- #

def _json_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def save_json(key: str, obj) -> Path:
    p = _json_path(key)
    p.write_text(json.dumps(obj))
    return p


def load_json(key: str):
    p = _json_path(key)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def df_path(key: str) -> Path:
    return cache_dir() / f"{key}.parquet"


def save_df(key: str, df: pd.DataFrame) -> Path:
    p = df_path(key)
    df.to_parquet(p, index=False)
    return p


def load_df(key: str) -> pd.DataFrame | None:
    p = df_path(key)
    if not p.exists():
        return None
    return pd.read_parquet(p)

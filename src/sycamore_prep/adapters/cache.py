"""Parquet cache keyed by ticker + concept + period. Provider-agnostic.

Tidy tabular pulls are cached as parquet; raw nested API payloads (EDGAR
submissions JSON, full-text-search hits) are cached as JSON sidecars so they
can be replayed offline and audited.
"""

from __future__ import annotations

import json
import re
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


# --------------------------------------------------------------------------- #
# Prediction-market snapshots (Polymarket overlay)
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


def _text_path(key: str) -> Path:
    return cache_dir() / f"{key}.txt"


def save_text(key: str, text: str) -> Path:
    p = _text_path(key)
    p.write_text(text, encoding="utf-8")
    return p


def load_text(key: str) -> str | None:
    p = _text_path(key)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Volatility snapshots (tastytrade overlay) — rolling daily history
# --------------------------------------------------------------------------- #

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


def list_market_snapshots(slug: str) -> list[tuple[str, Path]]:
    """All cached snapshots for a slug as (as_of_date, path), oldest first.
    The date is the trailing `_YYYY-MM-DD` segment of the filename."""
    key = _slug_key(slug)
    out: list[tuple[str, Path]] = []
    for p in sorted(cache_dir().glob(f"event_{key}_*.parquet")):
        out.append((p.stem.rsplit("_", 1)[-1], p))
    return out

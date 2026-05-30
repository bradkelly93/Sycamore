"""Config loader. Single source of truth = ./config.yaml at the project root."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class EdgarConfig(BaseModel):
    user_agent: str
    base_url: str = "https://data.sec.gov"
    ticker_map_url: str = "https://www.sec.gov/files/company_tickers.json"
    rate_limit_rps: float = 8.0


class CacheConfig(BaseModel):
    dir: str = "data/cache"


class RawConfig(BaseModel):
    dir: str = "data/raw"
    iws_holdings: str = "iws_holdings.csv"
    iwn_holdings: str = "iwn_holdings.csv"
    sycamore_holdings: str = "sycamore_holdings.csv"


class ValuationConfig(BaseModel):
    wacc: float = 0.09
    terminal_growth: float = 0.025
    forecast_years: int = 10


class TastytradeConfig(BaseModel):
    """Non-secret settings for the volatility overlay.

    Credentials are NEVER stored here (config.yaml is committed) — set
    TASTYTRADE_USERNAME / TASTYTRADE_PASSWORD in the environment instead.
    """

    base_url: str = "https://api.tastytrade.com"
    user_agent: str = "sycamore-prep/0.1 (volatility-overlay)"
    horizon_days: int = 30


class AppConfig(BaseModel):
    edgar: EdgarConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    raw: RawConfig = Field(default_factory=RawConfig)
    valuation: ValuationConfig = Field(default_factory=ValuationConfig)
    tastytrade: TastytradeConfig = Field(default_factory=TastytradeConfig)
    peers: dict[str, list[str]] = Field(default_factory=dict)
    test_tickers: list[str] = Field(default_factory=list)


def project_root() -> Path:
    # config.py lives at src/sycamore_prep/config.py — root is two parents up.
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path else project_root() / "config.yaml"
    with cfg_path.open("r") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    return AppConfig(**raw)


def cache_dir() -> Path:
    p = project_root() / load_config().cache.dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def raw_dir() -> Path:
    p = project_root() / load_config().raw.dir
    p.mkdir(parents=True, exist_ok=True)
    return p

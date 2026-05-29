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
    fts_base_url: str = "https://efts.sec.gov/LATEST/search-index"
    rate_limit_rps: float = 8.0


class SpinoffsConfig(BaseModel):
    # Forms that register a spin-off SpinCo (Form 10 family). `forms=10-12B`
    # in full-text search returns base + /A amendments.
    forms: list[str] = Field(default_factory=lambda: ["10-12B"])
    # How far back `spinoffs scan` looks by default.
    lookback_days: int = 540
    # Near-universal phrase in a Form 10 information statement; used as the
    # full-text-search query so a forms-only scan still returns hits.
    query: str = "information statement"


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


class AppConfig(BaseModel):
    edgar: EdgarConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    raw: RawConfig = Field(default_factory=RawConfig)
    valuation: ValuationConfig = Field(default_factory=ValuationConfig)
    spinoffs: SpinoffsConfig = Field(default_factory=SpinoffsConfig)
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

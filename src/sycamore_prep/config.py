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


class TastytradeConfig(BaseModel):
    """Non-secret settings for the volatility overlay.

    Credentials are NEVER stored here (config.yaml is committed) — set
    TASTYTRADE_CLIENT_SECRET / TASTYTRADE_REFRESH_TOKEN in the environment
    (OAuth2; username/password session-tokens were discontinued 2025-12-01).
    """

    base_url: str = "https://api.tastyworks.com"
    user_agent: str = "sycamore-prep/0.1 (volatility-overlay)"
    api_version: str = "20251101"
    horizon_days: int = 30


class TradingViewConfig(BaseModel):
    """Declarative replica of the analyst's own saved TradingView technical
    screen. NON-PRIMARY context overlay only — never enters the score (see
    CLAUDE.md: bottom-up, downside-first). `filters` are AND-combined.
    """
    enabled: bool = False
    mode: str = "api"                                        # "api" (live screener) | "csv" (dropped file)
    csv_file: str = "tradingview_screen.csv"                 # under data/raw/ when mode == "csv"
    signal_column: str = ""                                  # CSV label column, e.g. "regime"
    pass_values: list[str] = Field(default_factory=list)     # label values that count as "passes"
    region: str = "america"
    select: list[str] = Field(default_factory=list)          # carried indicators
    filters: list[dict] = Field(default_factory=list)        # {field, op, value}
    cache_ttl_minutes: int = 1440                            # EOD-flavored snapshot
    max_results: int = 20000
    divergence_strong_pctile: float = 0.70                   # cut on PURE composite
    sessionid: str | None = None                            # realtime needs your own


class PolymarketConfig(BaseModel):
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    rate_limit_rps: float = 5.0
    user_agent: str = "sycamore-prep/0.1"
    # Discovery: minimum relevance (0–1) to auto-include a candidate match.
    min_relevance: float = 0.45


class MacroMarketSpec(BaseModel):
    query: str
    # GICS sectors this macro market bears on; ["*"] = applies to every name.
    applies_to: list[str] = Field(default_factory=lambda: ["*"])


# Category/tag tokens that mark a prediction market as off-thesis noise
# (crypto, sports/esports, pop-culture) for a bottom-up equity pitch. Applied
# only in the company + peer apertures — the macro aperture intentionally keeps
# crypto (a long-dated BTC market is a risk-appetite/liquidity gauge there).
# This is the schema default; config.yaml's `prediction.noise_category_tokens`
# overrides it so the list is tunable without a code change.
DEFAULT_NOISE_CATEGORY_TOKENS: list[str] = [
    "crypto", "bitcoin", "ethereum", "solana", "dogecoin", "xrp", "bnb",
    "sports", "esports", "soccer", "tennis", "basketball", "baseball",
    "hockey", "football", "nba", "nfl", "mlb", "nhl", "ufc", "mma", "golf",
    "celebrities", "celebrity", "music", "culture", "entertainment", "movies",
]


class PredictionConfig(BaseModel):
    # Editable ticker→market mapping, under data/raw/.
    mapping_csv: str = "prediction_markets.csv"
    # Industry aperture: GICS sector → keyword searches.
    sector_keywords: dict[str, list[str]] = Field(default_factory=dict)
    # Macro aperture: broad markets + the sectors they read through to.
    macro_markets: list[MacroMarketSpec] = Field(default_factory=list)
    # Crypto/sports/pop-culture tags dropped in the company + peer apertures.
    noise_category_tokens: list[str] = Field(
        default_factory=lambda: list(DEFAULT_NOISE_CATEGORY_TOKENS)
    )
    # Cap on candidate markets kept per search query, after relevance filtering.
    # Threshold-laddered markets (e.g. "Will Bitcoin reach $100k/$110k/.../$500k"
    # or the WTI strike ladder) otherwise flood a name with dozens of near-identical
    # rows; keep the top-N by relevance, tie-broken by traded volume (depth =
    # credibility). 0 disables the cap.
    max_markets_per_query: int = 3
    # A confirmed RISK market at/above this implied prob, on an otherwise
    # high-ranked name, is surfaced as a screener contradiction flag.
    contradiction_prob: float = 0.20


class AppConfig(BaseModel):
    edgar: EdgarConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    raw: RawConfig = Field(default_factory=RawConfig)
    valuation: ValuationConfig = Field(default_factory=ValuationConfig)
    spinoffs: SpinoffsConfig = Field(default_factory=SpinoffsConfig)
    tastytrade: TastytradeConfig = Field(default_factory=TastytradeConfig)
    tradingview: TradingViewConfig = Field(default_factory=TradingViewConfig)
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    prediction: PredictionConfig = Field(default_factory=PredictionConfig)
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


def models_dir() -> Path:
    """Where generated model scaffolds (<TICKER>_model.xlsx) are written. The
    committed contents are MODEL_NOTES.md + templates/; *.xlsx here is
    gitignored (real pulled data)."""
    p = project_root() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def mapping_csv_path() -> Path:
    """Editable ticker→prediction-market mapping CSV (data/raw/)."""
    return raw_dir() / load_config().prediction.mapping_csv

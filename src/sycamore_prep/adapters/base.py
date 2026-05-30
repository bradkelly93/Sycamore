"""Adapter interfaces. A future FactSet/Bloomberg provider drops in here."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

import pandas as pd


# Canonical schema for the tidy financials frame every FundamentalsProvider
# must return. Downstream code (metrics, screener, comps) depends on these
# columns only — never on a provider-specific shape.
FINANCIALS_COLUMNS = [
    "ticker",
    "concept",      # canonical concept name (e.g., "Revenues", "NetIncomeLoss")
    "period",       # period end date (YYYY-MM-DD)
    "fy",           # fiscal year (int)
    "fp",           # fiscal period: "FY", "Q1", "Q2", "Q3", "Q4"
    "form",         # filing form: "10-K", "10-Q", ...
    "value",        # numeric value in reporting units
    "unit",         # e.g., "USD", "USD/shares", "shares"
    "source",       # provider tag, e.g., "edgar (primary)"
    "period_start", # period start (None for instant concepts like Assets)
    "period_days",  # end - start in days; None for instant. Used to filter
                    # filer-mis-tagged quarterly values out of FY queries.
]
FINANCIALS_COLUMNS_REQUIRED = [
    "ticker", "concept", "period", "fy", "fp", "form",
    "value", "unit", "source",
]


@dataclass(frozen=True)
class CompanyMeta:
    ticker: str
    cik: str
    name: str
    sic: str | None = None
    source: str = ""


class FinancialsFrame:
    """Thin wrapper around a tidy DataFrame to enforce schema.

    Required columns must be present. Optional columns (period_start,
    period_days) are added as null if missing — keeps old cached parquets
    and test fixtures readable while the duration-based FY filter is opt-in.
    """

    def __init__(self, df: pd.DataFrame):
        missing = [c for c in FINANCIALS_COLUMNS_REQUIRED if c not in df.columns]
        if missing:
            raise ValueError(f"FinancialsFrame missing columns: {missing}")
        df = df.copy()
        if "period_start" not in df.columns:
            df["period_start"] = pd.NA
        if "period_days" not in df.columns:
            df["period_days"] = pd.NA
        self.df = df[FINANCIALS_COLUMNS].copy()

    def concept(self, name: str, fp: str | None = "FY") -> pd.DataFrame:
        out = self.df[self.df["concept"] == name]
        if fp is not None:
            out = out[out["fp"] == fp]
        return out.sort_values("period")

    def latest(self, name: str, fp: str | None = "FY") -> float | None:
        sub = self.concept(name, fp=fp)
        if sub.empty:
            return None
        return float(sub.iloc[-1]["value"])


class FundamentalsProvider(ABC):
    """Primary-source financial statement data."""

    name: str = "base"

    @abstractmethod
    def get_company_meta(self, ticker: str) -> CompanyMeta: ...

    @abstractmethod
    def get_financials(
        self,
        ticker: str,
        concepts: Iterable[str] | None = None,
    ) -> FinancialsFrame:
        """Return tidy financials for a single ticker.

        If `concepts` is None, the provider returns its full canonical set.
        """


class PriceProvider(ABC):
    """Market data: prices, market cap, shares. Convenience source — tag it."""

    name: str = "base"

    @abstractmethod
    def get_prices(self, ticker: str, start: str | None = None) -> pd.DataFrame: ...

    @abstractmethod
    def get_market_cap(self, ticker: str) -> float | None: ...

    @abstractmethod
    def get_shares(self, ticker: str) -> float | None: ...


# Canonical schema for the raw markets frame every EventProbabilityProvider
# returns. Prediction-market odds are NON-PRIMARY (crowd opinion, not filings),
# so every row carries a `source` tag and is never spliced into a fundamentals
# frame. The discovery/overlay layer (prediction_markets/) enriches these rows
# with ticker, aperture, relevance_score, and read_through — providers stay
# ignorant of portfolio context so a Kalshi/Metaculus provider drops in here
# unchanged (Swappable-data-layer principle).
MARKET_COLUMNS = [
    "slug",            # stable market identifier / URL slug
    "question",        # full market question text
    "outcome",         # the outcome this row prices (e.g., "Yes")
    "implied_prob",    # 0–1 implied probability for `outcome` (price ≈ prob)
    "volume",          # cumulative traded volume in USD (depth / credibility)
    "liquidity",       # current order-book liquidity in USD, if provided
    "resolution_date", # market end / resolution date (YYYY-MM-DD) or None
    "category",        # provider category/tag string, if any (discovery hint)
    "url",             # canonical market URL
    "as_of",           # snapshot timestamp (UTC ISO-8601) — prices move
    "source",          # provider tag, e.g., "polymarket (non-primary)"
]


class EventProbabilityProvider(ABC):
    """Prediction-market implied probabilities (Polymarket, Kalshi, …).

    NON-PRIMARY by construction: these are crowd odds, not filings. Every
    returned row carries a `source` tag so downstream code can never confuse an
    implied probability with a primary-source fundamental. READ-ONLY — this
    interface exposes no trading or order-placement methods by design (live
    trading is an explicit CLAUDE.md non-goal).
    """

    name: str = "base"

    @abstractmethod
    def search_markets(
        self,
        query: str,
        *,
        active_only: bool = True,
        limit: int = 50,
    ) -> pd.DataFrame:
        """Keyword-search markets. Returns a tidy frame with MARKET_COLUMNS
        (one row per outcome). Used by the discovery layer to propose
        ticker→market matches with a relevance score."""

    @abstractmethod
    def get_market(self, slug: str) -> pd.DataFrame:
        """Current snapshot for a single market slug — one row per outcome,
        MARKET_COLUMNS schema. Used by the overlay to refresh implied
        probabilities for confirmed mappings."""

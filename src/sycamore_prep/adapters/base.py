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
]


@dataclass(frozen=True)
class CompanyMeta:
    ticker: str
    cik: str
    name: str
    sic: str | None = None
    source: str = ""


class FinancialsFrame:
    """Thin wrapper around a tidy DataFrame to enforce schema."""

    def __init__(self, df: pd.DataFrame):
        missing = [c for c in FINANCIALS_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"FinancialsFrame missing columns: {missing}")
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

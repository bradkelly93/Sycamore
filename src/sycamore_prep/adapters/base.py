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


# Tidy schema for a filer's filing index (one row per filing). Downstream
# spin-off code depends only on these columns — never on the raw SEC shape.
FILINGS_COLUMNS = [
    "cik",                     # 10-digit zero-padded
    "ticker",                  # may be None (filer has no ticker / not resolved)
    "form",                    # e.g. 10-12B, 10-12B/A, 8-K, 10-K
    "filing_date",             # YYYY-MM-DD
    "report_date",             # YYYY-MM-DD or None
    "accession",               # 0000000000-00-000000
    "primary_document",        # primary doc filename
    "primary_doc_description",
    "items",                   # 8-K item codes, comma-joined ("" if none)
    "is_xbrl",                 # bool
    "filing_url",              # canonical SEC Archives URL to the primary doc
    "source",                  # provider tag, e.g. "edgar (primary)"
]

# Tidy schema for a full-text-search hit (one row per matched filing).
SEARCH_COLUMNS = [
    "cik", "name", "ticker", "form", "root_form",
    "file_date", "accession", "primary_document", "sic",
    "filing_url", "source",
]


class FilingsProvider(ABC):
    """Filing-index + full-text-search surface (distinct from XBRL facts).

    Kept separate from FundamentalsProvider so a future FactSet/Bloomberg
    drop-in can implement filing discovery without reimplementing XBRL facts,
    and vice-versa (CLAUDE.md principle 5 — swappable data layer). Both return
    tidy DataFrames with a `source` column like every other adapter.
    """

    name: str = "base"

    @abstractmethod
    def get_submissions(self, ticker_or_cik: str, *, use_cache: bool = True) -> pd.DataFrame:
        """Tidy filing index (one row per filing) for a single filer.

        Columns: FILINGS_COLUMNS.
        """

    @abstractmethod
    def search_filings(
        self,
        *,
        forms: list[str],
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Full-text-search filings market-wide. Columns: SEARCH_COLUMNS."""

"""SEC EDGAR XBRL company-facts adapter. Primary-source fundamentals.

Endpoints (verify before relying on changes):
    https://www.sec.gov/files/company_tickers.json
    https://data.sec.gov/submissions/CIK{cik10}.json
    https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json

SEC requires:
  - a descriptive `User-Agent` with a real contact string
  - ~10 req/s max (we self-throttle below that)
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, Iterable

import pandas as pd
import requests

from ..config import load_config
from . import cache
from .base import CompanyMeta, FinancialsFrame, FundamentalsProvider


SOURCE_TAG = "edgar (primary)"


# Canonical concept set we pull by default. Keys are the canonical names we
# expose downstream; values are the US-GAAP XBRL tags we look for (in order).
# Where a company reports under one synonym but not another, the first hit
# wins. Bank-specific tags are included for Module C/E.
CANONICAL_CONCEPTS: dict[str, list[str]] = {
    # Income statement
    "Revenues":                ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "CostOfRevenue":           ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "GrossProfit":             ["GrossProfit"],
    "OperatingIncomeLoss":     ["OperatingIncomeLoss"],
    "InterestExpense":         ["InterestExpense"],
    "IncomeTaxExpenseBenefit": ["IncomeTaxExpenseBenefit"],
    "NetIncomeLoss":           ["NetIncomeLoss"],
    "EpsBasic":                ["EarningsPerShareBasic"],
    "EpsDiluted":              ["EarningsPerShareDiluted"],

    # Balance sheet
    "Assets":                  ["Assets"],
    "AssetsCurrent":           ["AssetsCurrent"],
    "Liabilities":             ["Liabilities"],
    "LiabilitiesCurrent":      ["LiabilitiesCurrent"],
    "StockholdersEquity":      ["StockholdersEquity",
                                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "CashAndEquivalents":      ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
    "LongTermDebt":            ["LongTermDebtNoncurrent", "LongTermDebt"],
    "ShortTermDebt":           ["ShortTermBorrowings", "DebtCurrent"],
    "Goodwill":                ["Goodwill"],
    "IntangibleAssetsNet":     ["IntangibleAssetsNetExcludingGoodwill"],

    # Cash flow
    "OperatingCashFlow":       ["NetCashProvidedByUsedInOperatingActivities"],
    "CapEx":                   ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "DepreciationAndAmortization": ["DepreciationDepletionAndAmortization",
                                    "DepreciationAmortizationAndAccretionNet",
                                    "DepreciationAndAmortization",
                                    "DepreciationAmortizationAndDepletionNet",
                                    "Depreciation"],
    "Dividends":               ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],

    # Shares
    "SharesOutstanding":       ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
    "WeightedAverageSharesDiluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],

    # Bank-specific
    "InterestIncome":          ["InterestAndDividendIncomeOperating", "InterestIncomeOperating"],
    "InterestExpenseBank":     ["InterestExpense"],
    "NetInterestIncome":       ["InterestIncomeExpenseNet"],
    "LoansAndLeasesReceivableNet": ["LoansAndLeasesReceivableNetReportedAmount",
                                    "NotesReceivableNet"],
    "Deposits":                ["Deposits"],
    "AllowanceForLoanAndLeaseLosses": ["FinancingReceivableAllowanceForCreditLosses",
                                       "LoansAndLeasesReceivableAllowance"],
    "NonperformingLoans":      ["FinancingReceivableImpairedLoansWithRelatedAllowance",
                                "FinancialInstrumentsOwnedAndPledgedAsCollateralAtFairValueNonperformingLoans"],
    "ProvisionForLoanLosses":  ["ProvisionForLoanLeaseAndOtherLosses",
                                "ProvisionForLoanAndLeaseLosses"],
    "NetChargeOffs":           ["FinancingReceivableAllowanceForCreditLossesWriteOffs"],
}


class EdgarProvider(FundamentalsProvider):
    name = "edgar"

    def __init__(self, user_agent: str | None = None, rate_limit_rps: float | None = None):
        cfg = load_config()
        self._ua = user_agent or cfg.edgar.user_agent
        self._rps = rate_limit_rps or cfg.edgar.rate_limit_rps
        self._base = cfg.edgar.base_url
        self._ticker_map_url = cfg.edgar.ticker_map_url
        self._last_call: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._ua,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        })

    # ---------- HTTP ----------

    def _throttle(self) -> None:
        min_interval = 1.0 / float(self._rps)
        delta = time.monotonic() - self._last_call
        if delta < min_interval:
            time.sleep(min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, url: str) -> dict[str, Any]:
        self._throttle()
        # `requests.get` is fine even though we set Host on the session — the
        # Host header may not match for the static www.sec.gov endpoint, so
        # override per-call.
        host = "www.sec.gov" if "www.sec.gov" in url else "data.sec.gov"
        headers = {"User-Agent": self._ua, "Host": host}
        resp = self._session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ---------- Ticker → CIK ----------

    @lru_cache(maxsize=1)
    def _ticker_to_cik_map(self) -> dict[str, dict[str, Any]]:
        data = self._get(self._ticker_map_url)
        # company_tickers.json is keyed by string indices.
        out: dict[str, dict[str, Any]] = {}
        for _, row in data.items():
            t = str(row["ticker"]).upper()
            out[t] = {
                "cik": str(row["cik_str"]).zfill(10),
                "name": row.get("title", ""),
            }
        return out

    def _resolve(self, ticker: str) -> tuple[str, str]:
        t = ticker.upper()
        m = self._ticker_to_cik_map()
        if t not in m:
            # Some tickers (e.g., MOG.A, BRK.B) use dots; SEC uses no separator.
            t_alt = t.replace(".", "-")
            if t_alt in m:
                t = t_alt
            else:
                raise KeyError(f"Ticker {ticker} not found in SEC ticker map.")
        return m[t]["cik"], m[t]["name"]

    # ---------- Public ----------

    def get_company_meta(self, ticker: str) -> CompanyMeta:
        cik, name = self._resolve(ticker)
        return CompanyMeta(ticker=ticker.upper(), cik=cik, name=name, source=SOURCE_TAG)

    def get_financials(
        self,
        ticker: str,
        concepts: Iterable[str] | None = None,
        use_cache: bool = True,
    ) -> FinancialsFrame:
        ticker_u = ticker.upper()
        if use_cache:
            cached = cache.load_financials(ticker_u)
            if cached is not None and not cached.empty:
                if concepts is not None:
                    cached = cached[cached["concept"].isin(list(concepts))]
                return FinancialsFrame(cached)

        cik, _ = self._resolve(ticker_u)
        url = f"{self._base}/api/xbrl/companyfacts/CIK{cik}.json"
        facts_json = self._get(url)
        df = _parse_company_facts(ticker_u, facts_json, CANONICAL_CONCEPTS)

        cache.save_financials(ticker_u, df)
        if concepts is not None:
            df = df[df["concept"].isin(list(concepts))]
        return FinancialsFrame(df)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _parse_company_facts(
    ticker: str,
    facts_json: dict[str, Any],
    concept_map: dict[str, list[str]],
) -> pd.DataFrame:
    """Flatten SEC companyfacts JSON into the canonical tidy schema.

    Strategy: for each canonical concept, scan its candidate US-GAAP tags in
    order and take the first one that has data. Within a tag, prefer the most
    recently-reported value for any given (fy, fp, form) tuple — SEC sometimes
    publishes restatements.
    """
    facts = facts_json.get("facts", {}).get("us-gaap", {})
    rows: list[dict[str, Any]] = []

    for canonical, candidates in concept_map.items():
        chosen = None
        for tag in candidates:
            if tag in facts:
                chosen = tag
                break
        if chosen is None:
            continue
        units = facts[chosen].get("units", {})
        # Pick the first unit (typically "USD" or "USD/shares" or "shares").
        if not units:
            continue
        for unit_name, entries in units.items():
            # Dedupe on (fy, fp, form) keeping the most recent `filed` date.
            by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
            for e in entries:
                key = (e.get("fy"), e.get("fp"), e.get("form"))
                prev = by_key.get(key)
                if prev is None or str(e.get("filed", "")) > str(prev.get("filed", "")):
                    by_key[key] = e
            for e in by_key.values():
                rows.append({
                    "ticker": ticker,
                    "concept": canonical,
                    "period": e.get("end"),
                    "fy": e.get("fy"),
                    "fp": e.get("fp"),
                    "form": e.get("form"),
                    "value": e.get("val"),
                    "unit": unit_name,
                    "source": SOURCE_TAG,
                })

    if not rows:
        return pd.DataFrame(columns=[
            "ticker", "concept", "period", "fy", "fp", "form",
            "value", "unit", "source",
        ])

    df = pd.DataFrame(rows)
    df["period"] = pd.to_datetime(df["period"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df.sort_values(["concept", "period"]).reset_index(drop=True)

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

import hashlib
import json
import re
import time
from functools import lru_cache
from typing import Any, Iterable
from urllib.parse import quote

import pandas as pd
import requests

from ..config import load_config
from . import cache
from .base import (
    CompanyMeta,
    FILINGS_COLUMNS,
    FilingsProvider,
    FinancialsFrame,
    FundamentalsProvider,
    SEARCH_COLUMNS,
)


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


class EdgarProvider(FundamentalsProvider, FilingsProvider):
    name = "edgar"

    def __init__(self, user_agent: str | None = None, rate_limit_rps: float | None = None):
        cfg = load_config()
        self._ua = user_agent or cfg.edgar.user_agent
        self._rps = rate_limit_rps or cfg.edgar.rate_limit_rps
        self._base = cfg.edgar.base_url
        self._ticker_map_url = cfg.edgar.ticker_map_url
        self._fts_base = cfg.edgar.fts_base_url
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
        host = (
            "efts.sec.gov" if "efts.sec.gov" in url
            else "www.sec.gov" if "www.sec.gov" in url
            else "data.sec.gov"
        )
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

    # ---------- Filings surface (submissions + full-text search) ----------

    def _to_cik(self, ticker_or_cik: str) -> tuple[str, str | None]:
        """Return (cik10, ticker|None). Accepts a raw CIK or a ticker."""
        s = str(ticker_or_cik).strip()
        if s.isdigit():
            return s.zfill(10), None
        cik, _ = self._resolve(s)
        return cik, s.upper()

    def get_submissions(self, ticker_or_cik: str, *, use_cache: bool = True) -> pd.DataFrame:
        cik, ticker = self._to_cik(ticker_or_cik)
        key = f"submissions_{cik}"
        raw = cache.load_json(key) if use_cache else None
        if raw is None:
            raw = self._get(f"{self._base}/submissions/CIK{cik}.json")
            cache.save_json(key, raw)
        return _flatten_submissions(cik, ticker, raw)

    def search_filings(
        self,
        *,
        forms: list[str],
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        q = query or "information statement"
        forms_str = ",".join(forms)
        key = "search_" + hashlib.md5(
            f"{q}|{forms_str}|{start}|{end}".encode()
        ).hexdigest()[:12]
        raw = cache.load_json(key) if use_cache else None
        if raw is None:
            raw = self._fetch_search_pages(q, forms_str, start, end)
            cache.save_json(key, raw)
        return _parse_search_hits(raw)

    def _fetch_search_pages(
        self,
        q: str,
        forms_str: str,
        start: str | None,
        end: str | None,
        page_cap: int = 10,
    ) -> dict[str, Any]:
        """Page through efts (100 hits/page) up to page_cap pages."""
        hits: list[dict[str, Any]] = []
        frm = 0
        total: int | None = None
        while True:
            url = (
                f"{self._fts_base}?q={quote(q)}&forms={forms_str}"
                + (f"&startdt={start}" if start else "")
                + (f"&enddt={end}" if end else "")
                + (f"&from={frm}" if frm else "")
            )
            data = self._get(url)
            page = (data.get("hits", {}) or {}).get("hits", []) or []
            hits.extend(page)
            if total is None:
                total = ((data.get("hits", {}) or {}).get("total", {}) or {}).get(
                    "value", len(hits)
                )
            frm += len(page)
            if not page or frm >= (total or 0) or frm >= page_cap * 100:
                break
        return {"hits": {"hits": hits, "total": {"value": total or len(hits)}}}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _period_days(start: Any, end: Any) -> int | None:
    """Days between start and end. Returns None for instant concepts (no start)."""
    if not start or not end:
        return None
    try:
        return int((pd.Timestamp(end) - pd.Timestamp(start)).days)
    except Exception:  # noqa: BLE001
        return None


def _is_annual_or_instant(start: Any, end: Any) -> bool:
    """True if this entry represents a full fiscal year (~12 months) or is
    an instant balance-sheet snapshot (no start). The window catches 52-week
    (364d) and 53-week (371d) fiscal years while rejecting both quarterly
    interim entries (~91d) AND multi-year cumulative entries that some
    filers publish in 10-K comparative tables (~730-1100d). Without an
    upper bound, a 3-year cumulative revenue row pollutes the latest-FY
    pick and silently breaks any ratio mixing it with annual flows.
    """
    d = _period_days(start, end)
    return d is None or 350 <= d <= 400


def _parse_company_facts(
    ticker: str,
    facts_json: dict[str, Any],
    concept_map: dict[str, list[str]],
) -> pd.DataFrame:
    """Flatten SEC companyfacts JSON into the canonical tidy schema.

    Three SEC quirks to handle:

    1. `fy` is the fiscal year of the FILING, not the period. A 10-K for
       FY2024 publishes 3 years of comparatives all tagged fy=2024. Correct
       dedupe key is `(end, fp, form)`: one row per period-end, latest filed
       wins (handles restatements; preserves every period).

    2. Multiple US-GAAP synonyms per concept. Different filers use different
       tags, and ONE filer may switch tags across years (CW moved revenue
       from SalesRevenueNet/Revenues to RevenueFromContractWithCustomer at
       ASC 606 in 2018). We pick the candidate that covers the most recent
       fiscal year, breaking ties by unique ANNUAL period-end count. The
       longest run of annual periods sits on the DEPRECATED tag, so ranking
       by count alone locks onto stale data that ends at the transition year
       (CW's SalesRevenueNet stops at 2017). See _pick_richest_synonym.

    3. Some filers mis-tag standalone quarterly values with fp=FY. We capture
       `start` from each entry and store `period_days = end - start` so the
       _series helper can filter FY queries to entries with span >= 350
       days (or instant snapshots with no start).
    """
    facts = facts_json.get("facts", {}).get("us-gaap", {})
    rows: list[dict[str, Any]] = []

    for canonical, candidates in concept_map.items():
        chosen, chosen_unit_entries = _pick_richest_synonym(facts, candidates)
        if chosen is None:
            continue
        for unit_name, entries in chosen_unit_entries.items():
            # Dedupe on (end, fp, form, start) keeping the most recent
            # `filed` date. `start` is in the key so a 365-day entry and a
            # multi-year cumulative entry for the same period_end don't
            # collapse to one arbitrary survivor — the access-time duration
            # filter in metrics._series picks the annual one.
            by_key: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
            for e in entries:
                key = (e.get("end"), e.get("fp"), e.get("form"), e.get("start"))
                prev = by_key.get(key)
                if prev is None or str(e.get("filed", "")) >= str(prev.get("filed", "")):
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
                    "period_start": e.get("start"),
                    "period_days": _period_days(e.get("start"), e.get("end")),
                })

    if not rows:
        return pd.DataFrame(columns=[
            "ticker", "concept", "period", "fy", "fp", "form",
            "value", "unit", "source", "period_start", "period_days",
        ])

    df = pd.DataFrame(rows)
    df["period"] = pd.to_datetime(df["period"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df.sort_values(["concept", "period"]).reset_index(drop=True)


def _pick_richest_synonym(
    facts: dict[str, Any],
    candidates: list[str],
) -> tuple[str | None, dict[str, list[dict[str, Any]]]]:
    """Return (chosen_tag, units_dict) for whichever candidate best represents
    the concept's CURRENT reporting basis.

    Ranking key per candidate: (latest annual period-end, count of unique
    annual period-ends). Recency dominates; annual count is only the tie-break.

    Why recency first: when a filer migrates tags across an accounting-standard
    change (e.g. ASC 605 `SalesRevenueNet` -> ASC 606
    `RevenueFromContractWithCustomer...`), the LONGEST run of annual periods
    sits on the DEPRECATED tag. Ranking by raw annual count therefore locks
    onto stale data that ends at the transition year — CW's `SalesRevenueNet`
    has 10 annual ends (2008-2017) and beat its current tag's 5 (2021-2025);
    CACI's legacy `Revenues` (2009-2018) beat its current tag by one. Preferring
    the tag that covers the most recent fiscal year keeps the canonical series
    on the live basis and keeps ONE consistent basis per series (no
    cross-standard splicing). Counting annual periods — not raw entries — for
    the tie-break means a tag cluttered with mis-tagged quarterly entries still
    can't outrank a clean annual tag of equal recency.
    """
    best_tag: str | None = None
    best_units: dict[str, list[dict[str, Any]]] = {}
    best_key: tuple[str, int] | None = None
    for tag in candidates:
        if tag not in facts:
            continue
        units = facts[tag].get("units", {}) or {}
        unique_annual_ends: set[str] = set()
        for entries in units.values():
            for e in entries:
                end = e.get("end")
                if end and _is_annual_or_instant(e.get("start"), end):
                    unique_annual_ends.add(end)
        # ISO date strings sort chronologically; "" sorts below any real date
        # so a candidate with only quarterly entries can't win over an annual
        # one but is still kept as a last resort if nothing else is present.
        latest_end = max(unique_annual_ends) if unique_annual_ends else ""
        key = (latest_end, len(unique_annual_ends))
        if best_key is None or key > best_key:
            best_key = key
            best_tag = tag
            best_units = units
    return best_tag, best_units


# --------------------------------------------------------------------------- #
# Filings parsing (submissions index + full-text-search hits)
# --------------------------------------------------------------------------- #

def _flatten_submissions(cik: str, ticker: str | None, raw: dict[str, Any]) -> pd.DataFrame:
    """Flatten the columnar `filings.recent` arrays into one row per filing.

    Only the `recent` block is read (covers ~1y / 1000 filings). Older filings
    paginate into `filings.files[]` shards — not fetched here; discovery leans
    on full-text search for historical spins.
    """
    if ticker is None:
        tks = raw.get("tickers") or []
        ticker = tks[0] if tks else None
    rec = (raw.get("filings", {}) or {}).get("recent", {}) or {}
    forms = rec.get("form", []) or []
    n = len(forms)

    def col(key: str) -> list:
        v = rec.get(key, []) or []
        return list(v) if len(v) == n else [None] * n

    accs, fdates, rdates = col("accessionNumber"), col("filingDate"), col("reportDate")
    pdocs, pdesc = col("primaryDocument"), col("primaryDocDescription")
    items, isx = col("items"), col("isXBRL")
    cik_int = int(cik)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        accn = accs[i]
        url = None
        if accn and pdocs[i]:
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{str(accn).replace('-', '')}/{pdocs[i]}"
            )
        rows.append({
            "cik": cik,
            "ticker": ticker,
            "form": forms[i],
            "filing_date": fdates[i],
            "report_date": rdates[i] or None,
            "accession": accn,
            "primary_document": pdocs[i],
            "primary_doc_description": pdesc[i],
            "items": items[i] or "",
            "is_xbrl": bool(isx[i]) if isx[i] is not None else False,
            "filing_url": url,
            "source": SOURCE_TAG,
        })
    return pd.DataFrame(rows, columns=FILINGS_COLUMNS)


# "Worthington Steel, Inc.  (WS)  (CIK 0001968487)" → name / ticker / cik.
# The ticker group is optional: a not-yet-trading SpinCo has no ticker.
_DISPLAY_NAME_RE = re.compile(
    r"^(?P<name>.*?)\s*(?:\((?P<ticker>[A-Z0-9.\-]{1,6})\)\s*)?\(CIK\s*(?P<cik>\d{4,10})\)\s*$"
)


def _parse_display_name(dn: str | None) -> tuple[str | None, str | None, str | None]:
    if not dn:
        return None, None, None
    m = _DISPLAY_NAME_RE.match(dn.strip())
    if not m:
        return dn.strip(), None, None
    return (m.group("name") or "").strip() or None, m.group("ticker"), m.group("cik")


def _parse_search_hits(raw: dict[str, Any]) -> pd.DataFrame:
    hits = (raw.get("hits", {}) or {}).get("hits", []) or []
    rows: list[dict[str, Any]] = []
    for h in hits:
        s = h.get("_source", {}) or {}
        names = s.get("display_names") or [""]
        name, ticker, dn_cik = _parse_display_name(names[0] if names else "")
        ciks = s.get("ciks") or []
        cik = (ciks[0] if ciks else dn_cik) or None
        cik = str(cik).zfill(10) if cik else None
        accn = s.get("adsh")
        _id = h.get("_id", "") or ""
        primary_doc = _id.split(":", 1)[1] if ":" in _id else None
        url = None
        if cik and accn and primary_doc:
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{str(accn).replace('-', '')}/{primary_doc}"
            )
        rows.append({
            "cik": cik,
            "name": name,
            "ticker": ticker,
            "form": s.get("file_type") or s.get("form"),
            "root_form": (s.get("root_forms") or [None])[0],
            "file_date": s.get("file_date"),
            "accession": accn,
            "primary_document": primary_doc,
            "sic": (s.get("sics") or [None])[0],
            "filing_url": url,
            "source": SOURCE_TAG,
        })
    return pd.DataFrame(rows, columns=SEARCH_COLUMNS)

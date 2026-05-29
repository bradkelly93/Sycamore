"""Spin-off discovery: turn EDGAR filing surfaces into SpinoffRecords.

Two modes (CLAUDE.md principle 5 — both ride the FilingsProvider adapter):

- ``scan_recent_form10s`` — market-wide via full-text search (efts). The
  10-12B is the registration vehicle for a spin-off SpinCo, so a forms=10-12B
  scan over a recent window surfaces candidate spins across the market.
- ``track_parent`` — a named parent's spin: full-text search for the SpinCo's
  10-12B (its information statement names the parent), or an explicit
  ``--spinco`` override, enriched with the SpinCo's submissions for status.

Downside-first + auditable: every record carries accession numbers + filing
URLs and a source tag; unknown fields stay ``None`` → rendered "pending"
downstream, never a silent zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

import pandas as pd

from ..adapters import EdgarProvider

SOURCE_TAG = "edgar (primary)"


class Status(str, Enum):
    """Spin-off lifecycle. Derived from which filings exist + dates."""

    ANNOUNCED = "announced"          # parent intent only, no SpinCo Form 10 yet
    FORM10_FILED = "form-10-filed"   # 10-12B / 10-12G registered
    AMENDED = "amended"              # >=1 10-12B/A
    EFFECTIVE = "effective"          # trading / SpinCo filing 10-Q, pre-first-10-K
    COMPLETED = "completed"          # SpinCo filing its own 10-K


@dataclass
class SpinoffRecord:
    # ---- Identity / linkage ----
    parent_name: str | None = None
    parent_cik: str | None = None
    parent_ticker: str | None = None
    spinco_name: str | None = None
    spinco_cik: str | None = None
    spinco_ticker: str | None = None        # None until it trades
    # ---- Filing trail ----
    form_type: str | None = None            # 10-12B family
    first_form10_date: str | None = None
    latest_amendment_date: str | None = None
    amendment_count: int = 0
    # ---- Soft fields (softfields extractor; fail-safe to None → "pending") ----
    record_date: str | None = None
    distribution_date: str | None = None
    distribution_ratio: str | None = None
    distribution_ratio_source: str | None = None
    # ---- Status + audit ----
    status: str = Status.FORM10_FILED.value
    sic: str | None = None
    accessions: list[str] = field(default_factory=list)
    filing_urls: list[str] = field(default_factory=list)
    information_statement_url: str | None = None  # resolved EX-99.1 (terms live here)
    # ---- SpinCo fundamentals (only when companyfacts XBRL exists) ----
    has_financials: bool = False
    net_debt_ebitda: float | None = None
    interest_coverage: float | None = None
    fcf: float | None = None
    fcf_margin: float | None = None
    roic: float | None = None
    revenue_cagr_3y: float | None = None
    negative_equity: bool | None = None
    ebitda_is_proxy: bool = False
    # ---- Three attributes (kept SEPARATE) + downside ----
    q1_better_business: str | None = None
    q2_valuation_disparity: str | None = None
    q3_improving_fundamentals: str | None = None
    downside_flags: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    sources: str = SOURCE_TAG
    error: str | None = None


# --------------------------------------------------------------------------- #
# Small NaN-safe scalar helpers (search/submissions frames use NaN for missing)
# --------------------------------------------------------------------------- #

def _scalar(v):
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _val(row: pd.Series, col: str):
    return _scalar(row[col]) if col in row.index else None


def _is_amendment(form) -> bool:
    return str(form).endswith("/A")


# --------------------------------------------------------------------------- #
# Status state machine
# --------------------------------------------------------------------------- #

def status_from_submissions(subs: pd.DataFrame | None, base_status: str) -> str:
    """Upgrade a base status using the SpinCo's own filings.

    A standalone 10-K means the spin is done and the SpinCo reports on its own
    → COMPLETED. A 10-Q without a 10-K means it is trading but pre-first-annual
    → EFFECTIVE. Otherwise the base status (FORM10_FILED / AMENDED) stands.
    """
    if subs is None or subs.empty:
        return base_status
    forms = {str(f) for f in subs["form"].tolist()}
    if "10-K" in forms:
        return Status.COMPLETED.value
    if any(f.startswith("10-Q") for f in forms):
        return Status.EFFECTIVE.value
    return base_status


# --------------------------------------------------------------------------- #
# Record builders
# --------------------------------------------------------------------------- #

def _records_from_search(hits: pd.DataFrame) -> list[SpinoffRecord]:
    """One SpinoffRecord per SpinCo (grouped by filer CIK) from efts hits."""
    recs: list[SpinoffRecord] = []
    if hits is None or hits.empty:
        return recs
    for cik, grp in hits.groupby("cik", dropna=False):
        grp = grp.sort_values("file_date")
        amend = grp[grp["form"].map(_is_amendment)]
        base = grp[~grp["form"].map(_is_amendment)]
        first_row = base.iloc[0] if not base.empty else grp.iloc[0]
        recs.append(SpinoffRecord(
            spinco_cik=_scalar(cik),
            spinco_name=_val(first_row, "name"),
            spinco_ticker=_val(first_row, "ticker"),
            sic=_val(first_row, "sic"),
            form_type=_val(first_row, "root_form") or _val(first_row, "form"),
            first_form10_date=_val(first_row, "file_date"),
            latest_amendment_date=(amend["file_date"].max() if not amend.empty else None),
            amendment_count=int(len(amend)),
            status=Status.AMENDED.value if len(amend) else Status.FORM10_FILED.value,
            accessions=[a for a in grp["accession"].tolist() if a],
            filing_urls=[u for u in grp["filing_url"].tolist() if isinstance(u, str)],
        ))
    return recs


def _record_from_submissions(
    subs: pd.DataFrame,
    name: str | None,
    cik: str | None,
    ticker: str | None,
) -> SpinoffRecord:
    """Build a record from a SpinCo's own submissions (the --spinco path)."""
    f10 = subs[subs["form"].astype(str).isin(["10-12B", "10-12B/A", "10-12G"])]
    f10 = f10.sort_values("filing_date")
    amend = f10[f10["form"].map(_is_amendment)]
    base = f10[~f10["form"].map(_is_amendment)]
    if f10.empty:
        base_status = Status.ANNOUNCED.value
    elif not amend.empty:
        base_status = Status.AMENDED.value
    else:
        base_status = Status.FORM10_FILED.value
    rec = SpinoffRecord(
        spinco_name=name,
        spinco_cik=cik or (_scalar(subs["cik"].iloc[0]) if not subs.empty else None),
        spinco_ticker=ticker or (_scalar(subs["ticker"].iloc[0]) if not subs.empty else None),
        form_type="10-12B" if not f10.empty else None,
        first_form10_date=(base["filing_date"].min() if not base.empty
                           else (f10["filing_date"].min() if not f10.empty else None)),
        latest_amendment_date=(amend["filing_date"].max() if not amend.empty else None),
        amendment_count=int(len(amend)),
        status=status_from_submissions(subs, base_status),
        accessions=[a for a in f10["accession"].tolist() if a],
        filing_urls=[u for u in f10["filing_url"].tolist() if isinstance(u, str)],
    )
    return rec


# --------------------------------------------------------------------------- #
# Public discovery entry points
# --------------------------------------------------------------------------- #

def scan_recent_form10s(
    edgar: EdgarProvider,
    *,
    forms: list[str],
    lookback_days: int,
    query: str,
    limit: int | None = None,
    use_cache: bool = True,
) -> list[SpinoffRecord]:
    """Market-wide discovery of recent Form 10 (10-12B) registrations."""
    end = date.today()
    start = end - timedelta(days=lookback_days)
    hits = edgar.search_filings(
        forms=forms, query=query, start=start.isoformat(), end=end.isoformat(),
        use_cache=use_cache,
    )
    recs = _records_from_search(hits)
    recs.sort(key=lambda r: (r.first_form10_date or ""), reverse=True)
    return recs[:limit] if limit else recs


def track_parent(
    edgar: EdgarProvider,
    parent: str,
    *,
    spinco: str | None = None,
    forms: list[str] | None = None,
    query: str | None = None,
    lookback_days: int = 1825,
    use_cache: bool = True,
) -> list[SpinoffRecord]:
    """Track a named parent's spin-off(s).

    With ``spinco`` (ticker or CIK) the linkage is explicit and deterministic.
    Without it, full-text search for the parent name + forms=10-12B surfaces the
    SpinCo's registration (its information statement names the parent) — a
    best-effort heuristic; prefer ``--spinco`` when known.
    """
    forms = forms or ["10-12B"]
    try:
        meta = edgar.get_company_meta(parent)
        p_name, p_cik, p_ticker = meta.name, meta.cik, parent.upper()
    except Exception as exc:  # noqa: BLE001
        return [SpinoffRecord(parent_ticker=parent.upper(),
                              error=f"parent resolve failed: {exc}")]

    if spinco is not None:
        sc_name = sc_cik = sc_ticker = None
        if not str(spinco).isdigit():
            try:
                m = edgar.get_company_meta(spinco)
                sc_name, sc_cik, sc_ticker = m.name, m.cik, spinco.upper()
            except Exception:  # noqa: BLE001 — fall back to submissions
                pass
        subs = edgar.get_submissions(spinco, use_cache=use_cache)
        recs = [_record_from_submissions(subs, sc_name, sc_cik, sc_ticker)]
    else:
        q = query or p_name.split()[0].title()
        end = date.today()
        start = end - timedelta(days=lookback_days)
        hits = edgar.search_filings(
            forms=forms, query=q, start=start.isoformat(), end=end.isoformat(),
            use_cache=use_cache,
        )
        recs = _records_from_search(hits)

    for r in recs:
        r.parent_name, r.parent_cik, r.parent_ticker = p_name, p_cik, p_ticker
        key = r.spinco_ticker or r.spinco_cik
        if spinco is None and key:
            try:
                r.status = status_from_submissions(
                    edgar.get_submissions(key, use_cache=use_cache), r.status
                )
            except Exception as exc:  # noqa: BLE001
                r.error = f"submissions enrich failed: {exc}"
    return recs

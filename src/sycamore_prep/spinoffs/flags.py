"""Three-attribute + value-trap flags for a tracked spin-off.

Downside-first: surface leverage / asset-quality flags from the SpinCo's
primary-source XBRL (reusing the Phase-2 metrics) at least as prominently as
upside. The three attributes are kept SEPARATE (CLAUDE.md principle 2 — never
one opaque score). When the SpinCo has no XBRL yet (freshly registered,
pre-first-10-K) the computed fields stay None and render "pending" — never a
silent zero.

Note (resolved by the data-shape pre-check): a SpinCo that has filed at least
one standalone 10-K HAS full companyfacts XBRL, so these metrics are reachable
via the synonym-aware EdgarProvider.get_financials — they must NEVER be read by
a single-tag lookup (VLTO/DHR freeze "Revenues" at an ASC-606 transition).
"""

from __future__ import annotations

import pandas as pd

from ..adapters.base import FinancialsFrame
from ..metrics import (
    ebitda_fy,
    fcf_margin_fy,
    free_cash_flow_fy,
    interest_coverage_fy,
    net_debt_to_ebitda_fy,
    revenue_fy,
    roic_fy,
)
from ..metrics.profitability import _series
from .discovery import SpinoffRecord

# Sycamore avoids these; surfaced at least as prominently as upside.
DOWNSIDE_THRESHOLDS = {
    "high_leverage_net_debt_ebitda": 4.0,
    "thin_interest_coverage": 3.0,
    "thin_fcf_margin": 0.02,
}

PENDING = "pending"


def _last(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _cagr(series: pd.Series, years: int) -> float | None:
    s = series.dropna()
    if len(s) < years + 1:
        return None
    start, end = s.iloc[-(years + 1)], s.iloc[-1]
    if pd.isna(start) or pd.isna(end) or start <= 0:
        return None
    return float((end / start) ** (1.0 / years) - 1.0)


def _pct(x: float | None) -> str | None:
    return None if x is None else f"{x * 100:.1f}%"


def _mult(x: float | None) -> str | None:
    return None if x is None else f"{x:.1f}x"


def apply_flags(record: SpinoffRecord, ff: FinancialsFrame | None) -> SpinoffRecord:
    """Populate a record's metric / flag / Q1 / Q3 fields from SpinCo XBRL.

    Mutates and returns ``record``. Q2 (valuation disparity) needs a market
    price and the forced-selling window — left to the tracker; here it is only
    set to "pending" when there are no financials at all.
    """
    if ff is None or ff.df.empty:
        record.has_financials = False
        record.q1_better_business = PENDING
        record.q3_improving_fundamentals = PENDING
        return record

    record.has_financials = True
    nde = _last(net_debt_to_ebitda_fy(ff))
    icov = _last(interest_coverage_fy(ff))
    fcf = _last(free_cash_flow_fy(ff))
    fcf_margin = _last(fcf_margin_fy(ff))
    roic = _last(roic_fy(ff))
    rev_cagr = _cagr(revenue_fy(ff), 3)
    equity = _last(_series(ff, "StockholdersEquity"))
    eb = ebitda_fy(ff)

    record.net_debt_ebitda = nde
    record.interest_coverage = icov
    record.fcf = fcf
    record.fcf_margin = fcf_margin
    record.roic = roic
    record.revenue_cagr_3y = rev_cagr
    record.negative_equity = (equity is not None and equity < 0)
    record.ebitda_is_proxy = (not eb.empty and str(eb.name).endswith("proxy_EBIT_only"))

    t = DOWNSIDE_THRESHOLDS
    flags: list[str] = []
    if nde is not None and nde > t["high_leverage_net_debt_ebitda"]:
        flags.append("high_leverage")
    if icov is not None and icov < t["thin_interest_coverage"]:
        flags.append("thin_interest_coverage")
    if record.negative_equity:
        flags.append("negative_equity")
    if fcf_margin is not None and fcf_margin < t["thin_fcf_margin"]:
        flags.append("negative_or_thin_fcf")
    if rev_cagr is not None and rev_cagr < 0:
        flags.append("declining_revenue")
    record.downside_flags = flags

    # Q1 better business / Q3 improving fundamentals — separate, human-readable.
    q1 = []
    if roic is not None:
        q1.append(f"ROIC {_pct(roic)}")
    if fcf_margin is not None:
        q1.append(f"FCF margin {_pct(fcf_margin)}")
    if nde is not None:
        proxy = " (EBIT proxy)" if record.ebitda_is_proxy else ""
        q1.append(f"net debt/EBITDA {_mult(nde)}{proxy}")
    record.q1_better_business = "; ".join(q1) if q1 else PENDING
    record.q3_improving_fundamentals = (
        f"revenue 3y CAGR {_pct(rev_cagr)}" if rev_cagr is not None else PENDING
    )
    return record

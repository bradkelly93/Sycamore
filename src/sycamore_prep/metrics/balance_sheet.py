"""Balance-sheet / leverage metrics. Downside-first surfacing per CLAUDE.md."""

from __future__ import annotations

import pandas as pd

from ..adapters.base import FinancialsFrame
from .profitability import _series, operating_margin_fy, revenue_fy


def total_debt_fy(ff: FinancialsFrame) -> pd.Series:
    lt = _series(ff, "LongTermDebt")
    st = _series(ff, "ShortTermDebt")
    if lt.empty and st.empty:
        return pd.Series(dtype=float, name="TotalDebt")
    idx = lt.index.union(st.index)
    return (lt.reindex(idx).fillna(0) + st.reindex(idx).fillna(0)).rename("TotalDebt")


def net_debt_fy(ff: FinancialsFrame) -> pd.Series:
    td = total_debt_fy(ff)
    cash = _series(ff, "CashAndEquivalents")
    if td.empty:
        return pd.Series(dtype=float, name="NetDebt")
    idx = td.index
    return (td - cash.reindex(idx).fillna(0)).rename("NetDebt")


def ebitda_fy(ff: FinancialsFrame) -> pd.Series:
    """EBITDA proxy = OperatingIncomeLoss + D&A.

    We don't have a clean D&A tag in our canonical set, so v1 returns just
    OperatingIncome and labels it EBIT. Phase 3 will layer in a proper EBITDA
    once we pull DepreciationAndAmortization. This is conservative — a higher
    EBITDA would *lower* the debt ratio, so EBIT-based debt/EBITDA over-states
    leverage, which is the right direction for a downside-first screen.
    """
    op = _series(ff, "OperatingIncomeLoss").rename("EBIT_as_EBITDA_proxy")
    return op


def net_debt_to_ebitda_fy(ff: FinancialsFrame) -> pd.Series:
    nd = net_debt_fy(ff)
    eb = ebitda_fy(ff)
    if nd.empty or eb.empty:
        return pd.Series(dtype=float, name="NetDebt_to_EBITDA")
    common = nd.index.intersection(eb.index)
    return (nd.loc[common] / eb.loc[common]).rename("NetDebt_to_EBITDA")


def interest_coverage_fy(ff: FinancialsFrame) -> pd.Series:
    """Interest coverage = OperatingIncome / InterestExpense."""
    op = _series(ff, "OperatingIncomeLoss")
    ie = _series(ff, "InterestExpense")
    if op.empty or ie.empty:
        return pd.Series(dtype=float, name="InterestCoverage")
    common = op.index.intersection(ie.index)
    out = op.loc[common] / ie.loc[common]
    return out.rename("InterestCoverage")


def tangible_book_value_fy(ff: FinancialsFrame) -> pd.Series:
    eq = _series(ff, "StockholdersEquity")
    gw = _series(ff, "Goodwill")
    intang = _series(ff, "IntangibleAssetsNet")
    if eq.empty:
        return pd.Series(dtype=float, name="TangibleBookValue")
    return (
        eq.sub(gw.reindex(eq.index).fillna(0))
          .sub(intang.reindex(eq.index).fillna(0))
    ).rename("TangibleBookValue")

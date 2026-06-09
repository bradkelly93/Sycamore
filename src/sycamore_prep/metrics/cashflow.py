"""Cash-flow metrics. FCF = CFO - CapEx (simple, defensible)."""

from __future__ import annotations

import pandas as pd

from ..adapters.base import FinancialsFrame
from .profitability import _series, revenue_fy, net_income_fy


def free_cash_flow_fy(ff: FinancialsFrame) -> pd.Series:
    """FCF = Operating Cash Flow - CapEx.

    Both inputs are reported as positive numbers in our tagging, so we
    subtract directly. If NO capex was found for the ticker, return empty
    rather than treating capex as zero — a silent zero makes FCF equal CFO,
    which materially overstates FCF (the wrong direction for a downside-first
    screen; this produced impossible 60%+ "FCF margins" for E&Ps before the
    oil-and-gas capex tag synonyms were added).
    """
    cfo = _series(ff, "OperatingCashFlow")
    capex = _series(ff, "CapEx")
    if cfo.empty or capex.empty:
        return pd.Series(dtype=float, name="FreeCashFlow")
    common = cfo.index
    capex_aligned = capex.reindex(common).fillna(0)
    return (cfo - capex_aligned).rename("FreeCashFlow")


def fcf_margin_fy(ff: FinancialsFrame) -> pd.Series:
    fcf = free_cash_flow_fy(ff)
    rev = revenue_fy(ff)
    if fcf.empty or rev.empty:
        return pd.Series(dtype=float, name="FCF_Margin")
    common = fcf.index.intersection(rev.index)
    return (fcf.loc[common] / rev.loc[common]).rename("FCF_Margin")


def fcf_conversion_fy(ff: FinancialsFrame) -> pd.Series:
    """FCF conversion = FCF / Net Income. <1 = quality concern."""
    fcf = free_cash_flow_fy(ff)
    ni = net_income_fy(ff)
    if fcf.empty or ni.empty:
        return pd.Series(dtype=float, name="FCF_Conversion")
    common = fcf.index.intersection(ni.index)
    return (fcf.loc[common] / ni.loc[common]).rename("FCF_Conversion")

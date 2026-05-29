"""Valuation metrics. Market data is non-primary — caller must keep source tags.

Per CLAUDE.md the discount-to-own-history percentile is the headline number,
so `percentile_vs_history` is the most important function here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..adapters.base import FinancialsFrame
from .balance_sheet import net_debt_fy, tangible_book_value_fy, ebitda_fy
from .cashflow import free_cash_flow_fy
from .profitability import _series, net_income_fy


def pe_fy(ff: FinancialsFrame, market_cap: float) -> pd.Series:
    """Trailing P/E = market_cap / NetIncome.

    Returns a single-period series indexed at the most-recent FY end so the
    output xlsx can show "current P/E" alongside historicals once we wire
    historical prices in Phase 3.
    """
    ni = net_income_fy(ff)
    if ni.empty:
        return pd.Series(dtype=float, name="PE")
    last_period = ni.index[-1]
    last_ni = ni.iloc[-1]
    if last_ni <= 0 or pd.isna(last_ni):
        return pd.Series([np.nan], index=[last_period], name="PE")
    return pd.Series([market_cap / last_ni], index=[last_period], name="PE")


def ev_ebitda_fy(ff: FinancialsFrame, market_cap: float) -> pd.Series:
    """EV / EBITDA. EV = market_cap + net debt. EBITDA proxy from balance_sheet."""
    eb = ebitda_fy(ff)
    nd = net_debt_fy(ff)
    if eb.empty:
        return pd.Series(dtype=float, name="EV_EBITDA")
    last_period = eb.index[-1]
    last_eb = eb.iloc[-1]
    last_nd = float(nd.reindex([last_period]).fillna(0).iloc[0]) if not nd.empty else 0.0
    if last_eb <= 0 or pd.isna(last_eb):
        return pd.Series([np.nan], index=[last_period], name="EV_EBITDA")
    ev = market_cap + last_nd
    return pd.Series([ev / last_eb], index=[last_period], name="EV_EBITDA")


def fcf_yield_fy(ff: FinancialsFrame, market_cap: float) -> pd.Series:
    """FCF yield = FCF / market_cap. Higher = cheaper."""
    fcf = free_cash_flow_fy(ff)
    if fcf.empty or market_cap <= 0:
        return pd.Series(dtype=float, name="FCF_Yield")
    last_period = fcf.index[-1]
    last_fcf = fcf.iloc[-1]
    return pd.Series([last_fcf / market_cap], index=[last_period], name="FCF_Yield")


def p_tbv_fy(ff: FinancialsFrame, market_cap: float) -> pd.Series:
    """Price / Tangible Book Value — the bank valuation metric."""
    tbv = tangible_book_value_fy(ff)
    if tbv.empty:
        return pd.Series(dtype=float, name="P_TBV")
    last_period = tbv.index[-1]
    last_tbv = tbv.iloc[-1]
    if last_tbv <= 0 or pd.isna(last_tbv):
        return pd.Series([np.nan], index=[last_period], name="P_TBV")
    return pd.Series([market_cap / last_tbv], index=[last_period], name="P_TBV")


def historical_fcf_yields(
    ff: FinancialsFrame,
    historical_market_caps: pd.Series,
) -> pd.Series:
    """Build a series of FCF_t / market_cap_t for each period in the overlap.

    `historical_market_caps` should be a Series indexed by FY period-end date
    (YYYY-MM-DD) — typically supplied by the caller using yfinance prices
    × shares outstanding from EDGAR.
    """
    fcf = free_cash_flow_fy(ff)
    if fcf.empty or historical_market_caps.empty:
        return pd.Series(dtype=float, name="FCF_Yield_History")
    common = fcf.index.intersection(historical_market_caps.index)
    if common.empty:
        return pd.Series(dtype=float, name="FCF_Yield_History")
    return (fcf.loc[common] / historical_market_caps.loc[common]).rename("FCF_Yield_History")


def percentile_vs_history(series: pd.Series, current: float) -> float:
    """Where does `current` sit in `series` as a 0-100 percentile?

    For FCF yield: higher = cheaper → percentile of 80 means today's yield
    is fatter than 80% of historical observations (cheap vs own history).
    For P/E, P/TBV: lower = cheaper, so call this with -current and -series
    or invert the interpretation downstream.

    Returns NaN if the series has fewer than 3 observations.
    """
    clean = series.dropna()
    if len(clean) < 3 or pd.isna(current):
        return float("nan")
    rank = (clean < current).sum() + 0.5 * (clean == current).sum()
    return float(100.0 * rank / len(clean))

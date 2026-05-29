"""Profitability metrics. All FY-indexed series unless noted.

Each function takes a FinancialsFrame and returns a pandas Series indexed by
the FY period-end date (YYYY-MM-DD string), values in reporting units.
Missing concepts return an empty Series — callers must handle that.
"""

from __future__ import annotations

import pandas as pd

from ..adapters.base import FinancialsFrame


def _series(ff: FinancialsFrame, concept: str, fp: str = "FY") -> pd.Series:
    sub = ff.concept(concept, fp=fp)
    if sub.empty:
        return pd.Series(dtype=float, name=concept)
    # Some filers mis-tag standalone quarterly values with fp=FY. When
    # period_days is available (instant concepts have it as null, flow
    # concepts as the calendar span), keep only annual flows (>=350 days)
    # or instants. This filter is only applied to fp=FY queries — quarterly
    # queries still see all matching rows.
    if fp == "FY" and "period_days" in sub.columns:
        days = pd.to_numeric(sub["period_days"], errors="coerce")
        sub = sub[days.isna() | (days >= 350)]
        if sub.empty:
            return pd.Series(dtype=float, name=concept)
    s = pd.Series(sub["value"].astype(float).values, index=sub["period"].values, name=concept)
    # Some restatements collide on the same period — keep the last (latest filed).
    return s[~s.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------- top of P&L

def revenue_fy(ff: FinancialsFrame) -> pd.Series:
    return _series(ff, "Revenues")


def gross_profit_fy(ff: FinancialsFrame) -> pd.Series:
    """GrossProfit if reported; else Revenues - CostOfRevenue."""
    gp = _series(ff, "GrossProfit")
    if not gp.empty:
        return gp
    rev = revenue_fy(ff)
    cog = _series(ff, "CostOfRevenue")
    common = rev.index.intersection(cog.index)
    return (rev.loc[common] - cog.loc[common]).rename("GrossProfit_derived")


def gross_margin_fy(ff: FinancialsFrame) -> pd.Series:
    rev = revenue_fy(ff)
    gp = gross_profit_fy(ff)
    common = rev.index.intersection(gp.index)
    if common.empty:
        return pd.Series(dtype=float, name="GrossMargin")
    return (gp.loc[common] / rev.loc[common]).rename("GrossMargin")


def operating_margin_fy(ff: FinancialsFrame) -> pd.Series:
    rev = revenue_fy(ff)
    op = _series(ff, "OperatingIncomeLoss")
    common = rev.index.intersection(op.index)
    if common.empty:
        return pd.Series(dtype=float, name="OperatingMargin")
    return (op.loc[common] / rev.loc[common]).rename("OperatingMargin")


def net_income_fy(ff: FinancialsFrame) -> pd.Series:
    return _series(ff, "NetIncomeLoss")


# ------------------------------------------------------------------ returns

def nopat_fy(ff: FinancialsFrame, tax_rate: float = 0.21) -> pd.Series:
    """NOPAT = Operating Income × (1 - effective tax rate).

    Statutory 21% is the v1 default. Future enhancement: derive effective
    tax rate from IncomeTaxExpenseBenefit / PreTaxIncome.
    """
    op = _series(ff, "OperatingIncomeLoss")
    return (op * (1 - tax_rate)).rename("NOPAT")


def invested_capital_fy(ff: FinancialsFrame) -> pd.Series:
    """Invested capital = Total debt + Stockholders' equity - Cash.

    Standard analyst convention; reasonable proxy when operating-lease detail
    isn't available. Returned as a period-end snapshot, NOT averaged with
    prior year — callers can average if they want.
    """
    eq = _series(ff, "StockholdersEquity")
    lt = _series(ff, "LongTermDebt")
    st = _series(ff, "ShortTermDebt")
    cash = _series(ff, "CashAndEquivalents")
    common = eq.index
    for s in (lt, st, cash):
        if not s.empty:
            common = common.intersection(s.index)
    if common.empty:
        return pd.Series(dtype=float, name="InvestedCapital")
    debt = lt.reindex(common).fillna(0) + st.reindex(common).fillna(0)
    return (debt + eq.loc[common] - cash.reindex(common).fillna(0)).rename("InvestedCapital")


def roic_fy(ff: FinancialsFrame, tax_rate: float = 0.21) -> pd.Series:
    """ROIC = NOPAT_t / average(InvestedCapital_t, InvestedCapital_{t-1}).

    Returns NaN for the first year (no prior to average with).
    """
    nopat = nopat_fy(ff, tax_rate=tax_rate)
    ic = invested_capital_fy(ff)
    if nopat.empty or ic.empty:
        return pd.Series(dtype=float, name="ROIC")
    ic_avg = (ic + ic.shift(1)) / 2.0
    common = nopat.index.intersection(ic_avg.index)
    return (nopat.loc[common] / ic_avg.loc[common]).rename("ROIC")


def roe_fy(ff: FinancialsFrame) -> pd.Series:
    """ROE = NetIncome_t / average(Equity_t, Equity_{t-1})."""
    ni = net_income_fy(ff)
    eq = _series(ff, "StockholdersEquity")
    if ni.empty or eq.empty:
        return pd.Series(dtype=float, name="ROE")
    eq_avg = (eq + eq.shift(1)) / 2.0
    common = ni.index.intersection(eq_avg.index)
    return (ni.loc[common] / eq_avg.loc[common]).rename("ROE")


def rotce_fy(ff: FinancialsFrame) -> pd.Series:
    """Return On Tangible Common Equity (the bank metric).

    TCE = StockholdersEquity - Goodwill - IntangibleAssetsNet.
    ROTCE = NetIncome / average(TCE_t, TCE_{t-1}).
    """
    ni = net_income_fy(ff)
    eq = _series(ff, "StockholdersEquity")
    gw = _series(ff, "Goodwill")
    intang = _series(ff, "IntangibleAssetsNet")
    if ni.empty or eq.empty:
        return pd.Series(dtype=float, name="ROTCE")
    tce = eq.sub(gw.reindex(eq.index).fillna(0)).sub(intang.reindex(eq.index).fillna(0))
    tce_avg = (tce + tce.shift(1)) / 2.0
    common = ni.index.intersection(tce_avg.index)
    return (ni.loc[common] / tce_avg.loc[common]).rename("ROTCE")


def gross_margin_stability(ff: FinancialsFrame, years: int = 5) -> float:
    """Stability score for gross margin over the last `years` years.

    Returns 1 - coefficient_of_variation. Higher = more stable. NaN if we
    don't have `years` data points.
    """
    gm = gross_margin_fy(ff).dropna()
    if len(gm) < years:
        return float("nan")
    window = gm.tail(years)
    mean = window.mean()
    if mean == 0:
        return float("nan")
    cv = window.std() / abs(mean)
    return float(1 - cv)

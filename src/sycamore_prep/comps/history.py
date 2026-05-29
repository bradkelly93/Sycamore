"""Historical multiple bands — the discount-to-own-history headline (CLAUDE.md).

Aligns cached yfinance daily closes (UNADJUSTED) to EDGAR FY-end dates, builds
per-period market caps (price × shares), and assembles P/E, EV/EBITDA and
FCF-yield bands (P/TBV for banks) plus where today's multiple sits as a
percentile of the stock's own history.

Why unadjusted close: paired with as-reported contemporaneous shares it gives
the true historical market cap (a split scales price and share count inversely,
so they cancel). Adjusted close would understate old market caps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..adapters.base import FinancialsFrame
from ..metrics.profitability import _series
from ..metrics.valuation import (
    cheapness_percentile,
    ev_ebitda_fy,
    ev_ebitda_history,
    fcf_yield_fy,
    historical_fcf_yields,
    p_tbv_fy,
    p_tbv_history,
    pe_fy,
    pe_history,
)

SHARE_BASES = ("wad", "shares_out", "eps_implied")


def shares_by_period(ff: FinancialsFrame, basis: str = "wad") -> tuple[pd.Series, str]:
    """Per-FY share count for historical market caps, with a fallback chain.

    Default 'wad' = weighted-average diluted (a duration aligned to each FY and
    the same denominator as diluted EPS). Falls back to period-end shares
    outstanding, then to NetIncome/EpsDiluted-implied shares. Returns (series,
    basis_label) so the choice is auditable in the output.
    """
    chain = {
        "wad": ["WeightedAverageSharesDiluted", "SharesOutstanding"],
        "shares_out": ["SharesOutstanding", "WeightedAverageSharesDiluted"],
        "eps_implied": [],
    }.get(basis, ["WeightedAverageSharesDiluted", "SharesOutstanding"])
    for tag in chain:
        s = _series(ff, tag).dropna()
        if not s.empty:
            return s.rename("shares"), tag
    ni, eps = _series(ff, "NetIncomeLoss"), _series(ff, "EpsDiluted")
    common = ni.index.intersection(eps.index)
    if not common.empty:
        implied = (ni.loc[common] / eps.loc[common]).replace([np.inf, -np.inf], np.nan).dropna()
        if not implied.empty:
            return implied.rename("shares"), "NetIncome/EpsDiluted (implied)"
    return pd.Series(dtype=float, name="shares"), "none"


def align_prices_to_fy_ends(
    prices: pd.DataFrame, period_ends: list[str], tolerance_days: int = 7
) -> pd.Series:
    """Unadjusted close on the last trading day on or before each FY-end.

    Backward as-of join (never looks forward → no lookahead bias); the
    `tolerance_days` window conservatively covers weekend/holiday FY-ends. A
    FY-end with no trade within tolerance (e.g. before price history begins)
    is dropped. Returns a Series indexed by the FY-end period strings.
    """
    if prices is None or prices.empty or not period_ends:
        return pd.Series(dtype=float, name="FY_End_Price")
    if "date" not in prices.columns or "close" not in prices.columns:
        return pd.Series(dtype=float, name="FY_End_Price")
    px = prices[["date", "close"]].copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    try:  # yfinance dates are often tz-aware; merge_asof needs matching tz.
        if px["date"].dt.tz is not None:
            px["date"] = px["date"].dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    px = px.dropna(subset=["date"]).sort_values("date")

    targets = pd.DataFrame({"period": list(period_ends)})
    targets["dt"] = pd.to_datetime(targets["period"], errors="coerce")
    targets = targets.dropna(subset=["dt"]).sort_values("dt")
    if px.empty or targets.empty:
        return pd.Series(dtype=float, name="FY_End_Price")

    merged = pd.merge_asof(
        targets, px, left_on="dt", right_on="date",
        direction="backward", tolerance=pd.Timedelta(days=tolerance_days),
    ).dropna(subset=["close"])
    return pd.Series(merged["close"].values, index=merged["period"].values,
                     name="FY_End_Price").astype(float)


@dataclass
class BandSummary:
    """One multiple's band. `percentile_cheap` reads HIGH = cheap vs own history
    (sign convention handled by cheapness_percentile). NaN with <3 observations."""
    multiple: str
    current: float
    n: int
    percentile_cheap: float
    p_min: float
    p25: float
    median: float
    p75: float
    p_max: float
    lower_is_cheap: bool
    series: pd.Series = field(default_factory=lambda: pd.Series(dtype=float), repr=False)


def _tail(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else float("nan")


def _summarize(name: str, series: pd.Series, current: float, lower_is_cheap: bool) -> BandSummary:
    s = series.dropna()
    n = int(len(s))
    pct = cheapness_percentile(s, current, lower_is_cheap)
    if n:
        q = s.quantile([0.0, 0.25, 0.5, 0.75, 1.0])
        pmin, p25, med, p75, pmax = (float(q.loc[0.0]), float(q.loc[0.25]),
                                     float(q.loc[0.5]), float(q.loc[0.75]), float(q.loc[1.0]))
    else:
        pmin = p25 = med = p75 = pmax = float("nan")
    cur = float(current) if current == current else float("nan")  # NaN-safe
    return BandSummary(name, cur, n, pct, pmin, p25, med, p75, pmax, lower_is_cheap, s)


@dataclass
class HistoryResult:
    ticker: str
    share_basis: str
    is_bank: bool
    fy_end_prices: pd.Series
    market_caps: pd.Series
    bands: dict[str, BandSummary]


def build_history(
    ff: FinancialsFrame,
    prices: pd.DataFrame,
    current_market_cap: float,
    is_bank: bool,
    ticker: str = "",
    basis: str = "wad",
) -> HistoryResult:
    """Assemble the per-FY market caps + multiple bands + current-vs-history
    percentiles. Current multiples reuse the existing *_fy valuation functions
    (fed the current market cap); the bands reuse the *_history extensions."""
    eps_idx = list(_series(ff, "EpsDiluted").index)
    oi_idx = list(_series(ff, "OperatingIncomeLoss").index)
    shares, basis_used = shares_by_period(ff, basis)
    master_ends = sorted(set(eps_idx) | set(oi_idx) | set(shares.index))

    fy_prices = align_prices_to_fy_ends(prices, master_ends)
    common = shares.index.intersection(fy_prices.index)
    market_caps = (fy_prices.loc[common] * shares.loc[common]).rename("MarketCap_History").sort_index() \
        if len(common) else pd.Series(dtype=float, name="MarketCap_History")

    bands: dict[str, BandSummary] = {}
    bands["PE"] = _summarize("PE", pe_history(ff, fy_prices),
                             _tail(pe_fy(ff, current_market_cap)), lower_is_cheap=True)
    if is_bank:
        bands["P_TBV"] = _summarize("P_TBV", p_tbv_history(ff, market_caps),
                                    _tail(p_tbv_fy(ff, current_market_cap)), lower_is_cheap=True)
    else:
        bands["EV_EBITDA"] = _summarize("EV_EBITDA", ev_ebitda_history(ff, market_caps),
                                        _tail(ev_ebitda_fy(ff, current_market_cap)), lower_is_cheap=True)
        bands["FCF_Yield"] = _summarize("FCF_Yield", historical_fcf_yields(ff, market_caps),
                                        _tail(fcf_yield_fy(ff, current_market_cap)), lower_is_cheap=False)

    return HistoryResult(ticker=ticker, share_basis=basis_used, is_bank=is_bank,
                         fy_end_prices=fy_prices, market_caps=market_caps, bands=bands)

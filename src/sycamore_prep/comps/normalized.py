"""Through-cycle normalized earnings.

Per the approved spec: 5-year average operating margin applied to current
revenue. This de-noises a trailing P/E distorted by a cyclical peak or trough
year. We also surface the TROUGH (min-margin) earnings as the explicit bear
anchor, so downside is visible alongside the mid-cycle figure (CLAUDE.md #1).

    norm_op_margin   = mean(OpInc_t / Rev_t over the last `window` FYs)
    norm_op_income   = norm_op_margin × Revenue_latest
    norm_net_income  = (norm_op_income − interest_expense_latest) × (1 − tax_rate)
    norm_eps         = norm_net_income / diluted_shares_latest
    norm_pe          = current_price / norm_eps

Interest expense is subtracted before tax so a levered name's normalized
earnings aren't overstated (which would understate the normalized P/E and make
the stock look cheaper than it is — against the downside-first principle).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.base import FinancialsFrame
from ..metrics.profitability import _series, operating_margin_fy, revenue_fy

DEFAULT_TAX_RATE = 0.21


@dataclass
class NormalizedEarnings:
    window: int
    n_used: int
    mean_op_margin: float
    trough_op_margin: float
    current_revenue: float
    interest_expense: float
    normalized_op_income: float
    normalized_net_income: float
    normalized_eps: float
    trough_net_income: float
    trough_eps: float
    normalized_pe: float
    trough_pe: float
    trailing_pe: float
    tax_rate: float
    basis: str


def _safe_div(a: float, b: float) -> float:
    return a / b if (b == b and b > 0 and a == a) else float("nan")


def normalized_earnings(
    ff: FinancialsFrame,
    current_price: float,
    window: int = 5,
    tax_rate: float = DEFAULT_TAX_RATE,
) -> NormalizedEarnings | None:
    """Compute 5y-normalized (and trough) earnings + P/E. Returns None if the
    required revenue / operating-margin history is missing."""
    rev = revenue_fy(ff).dropna()
    opm = operating_margin_fy(ff).dropna()
    if rev.empty or opm.empty:
        return None
    opm_w = opm.iloc[-window:]
    if opm_w.empty:
        return None

    mean_m = float(opm_w.mean())
    trough_m = float(opm_w.min())
    cur_rev = float(rev.iloc[-1])

    shares = _series(ff, "WeightedAverageSharesDiluted").dropna()
    if shares.empty:
        shares = _series(ff, "SharesOutstanding").dropna()
    sh = float(shares.iloc[-1]) if not shares.empty else float("nan")

    interest = _series(ff, "InterestExpense").dropna()
    int_exp = float(interest.iloc[-1]) if not interest.empty else 0.0

    norm_oi = mean_m * cur_rev
    norm_ni = (norm_oi - int_exp) * (1.0 - tax_rate)
    trough_ni = (trough_m * cur_rev - int_exp) * (1.0 - tax_rate)
    norm_eps = _safe_div(norm_ni, sh)
    trough_eps = _safe_div(trough_ni, sh)

    eps = _series(ff, "EpsDiluted").dropna()
    trail_eps = float(eps.iloc[-1]) if not eps.empty else float("nan")

    return NormalizedEarnings(
        window=window,
        n_used=int(len(opm_w)),
        mean_op_margin=mean_m,
        trough_op_margin=trough_m,
        current_revenue=cur_rev,
        interest_expense=int_exp,
        normalized_op_income=norm_oi,
        normalized_net_income=norm_ni,
        normalized_eps=norm_eps,
        trough_net_income=trough_ni,
        trough_eps=trough_eps,
        normalized_pe=_safe_div(current_price, norm_eps),
        trough_pe=_safe_div(current_price, trough_eps),
        trailing_pe=_safe_div(current_price, trail_eps),
        tax_rate=tax_rate,
        basis="(5y mean op margin x current revenue - interest) x (1 - tax)",
    )

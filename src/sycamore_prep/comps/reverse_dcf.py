"""Reverse DCF — FCFF, end-of-year discounting, Gordon terminal value.

Two complementary reads, both surfaced (never collapsed):
  (a) REVERSE — given today's enterprise value, what constant near-term FCFF
      growth is priced in? Compare to the franchise's realized growth: a much
      higher implied growth is a downside signal.
  (b) FORWARD — at a conservative assumed growth, what is fair value and the
      margin of safety vs today's price?

Downside-first per CLAUDE.md: cases run Bear -> Base -> Bull and each carries a
margin of safety alongside the implied-growth number. This is a pure-number
module — no network, no FinancialsFrame — so it unit-tests against hand-computed
values. The solver is a hand-rolled bisection: no scipy dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def dcf_enterprise_value(
    fcf0: float, growth: float, wacc: float, terminal_growth: float, years: int
) -> float:
    """PV of FCFF growing at constant `growth` for `years`, discounted
    end-of-year at `wacc`, plus a Gordon terminal value. Returns enterprise
    value (firm level, before subtracting net debt).

    End-of-year (not mid-year) discounting is the conservative choice: it yields
    a lower PV per unit of growth, so the reverse solve demands a *higher*
    implied growth to justify today's price — the downside-first reading.
    """
    if wacc <= terminal_growth:
        raise ValueError("wacc must exceed terminal_growth for a Gordon terminal value")
    pv = 0.0
    for t in range(1, years + 1):
        fcf_t = fcf0 * (1.0 + growth) ** t
        pv += fcf_t / (1.0 + wacc) ** t
    fcf_h = fcf0 * (1.0 + growth) ** years
    terminal = fcf_h * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv += terminal / (1.0 + wacc) ** years
    return pv


def dcf_equity_value_per_share(
    fcf0: float, growth: float, wacc: float, terminal_growth: float,
    years: int, net_debt: float, shares: float,
) -> float:
    """Forward fair value per share = (EV - net debt) / shares."""
    if shares <= 0:
        return float("nan")
    ev = dcf_enterprise_value(fcf0, growth, wacc, terminal_growth, years)
    return (ev - net_debt) / shares


def solve_implied_growth(
    target_ev: float, fcf0: float, wacc: float, terminal_growth: float, years: int,
    lo: float = -0.50, hi: float = 1.00, max_iter: int = 200,
) -> tuple[float, bool]:
    """Bisect for the constant FCFF growth s.t. dcf_enterprise_value == target_ev.

    dcf_enterprise_value is strictly increasing in growth (for fcf0 > 0), so the
    root is unique whenever target_ev lies within [EV(lo), EV(hi)]. If it lies
    outside that bracket, return the nearer edge with converged=False — no silent
    clamp; the caller surfaces the flag. Returns (nan, False) if fcf0 <= 0
    (reverse DCF is undefined on non-positive base FCF) or if wacc <= terminal.
    """
    if fcf0 <= 0 or wacc <= terminal_growth:
        return float("nan"), False

    def f(g: float) -> float:
        return dcf_enterprise_value(fcf0, g, wacc, terminal_growth, years) - target_ev

    if f(lo) > 0:
        return lo, False          # market prices in LESS growth than lo (cheap)
    if f(hi) < 0:
        return hi, False          # price unjustifiable even at hi growth (dear)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm == 0.0 or (hi - lo) < 1e-10:
            return mid, True
        if fm < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi), True


@dataclass
class DcfCase:
    """One scenario. `implied_growth` is the reverse read (priced-in growth);
    `fair_value_per_share` / `margin_of_safety` are the forward read at
    `assumed_growth`. MoS < 0 means the conservative DCF is below today's price.
    """
    label: str
    wacc: float
    terminal_growth: float
    fcf0: float
    implied_growth: float
    implied_converged: bool
    assumed_growth: float
    fair_value_per_share: float
    current_price: float
    margin_of_safety: float


def build_cases(
    current_market_cap: float,
    net_debt: float,
    shares: float,
    fcf0_base: float,
    wacc: float,
    terminal_growth: float,
    years: int,
    assumed_growth: float,
    fcf0_bear: float | None = None,
    fcf0_bull: float | None = None,
    current_price: float | None = None,
    wacc_stress: float = 0.01,
    tg_stress: float = 0.005,
) -> list[DcfCase]:
    """Bear -> Base -> Bull (downside first). Bear stresses WACC up, terminal
    growth down, and seeds off the conservative (trough/normalized) FCF; Bull
    does the reverse. `assumed_growth` is the conservative forward growth used
    for the fair-value / margin-of-safety read — typically the franchise's own
    realized FCF CAGR, clipped — shared across cases so the fan is comparable.

    `current_price` is the price the margin of safety is measured against; pass
    the actual quote so MoS reflects what an analyst sees. Falls back to
    market_cap / diluted shares when no quote is supplied.
    """
    current_ev = current_market_cap + net_debt
    if current_price is None or current_price != current_price or current_price <= 0:
        current_price = current_market_cap / shares if shares > 0 else float("nan")
    fcf0_bear = fcf0_base if fcf0_bear is None else fcf0_bear
    fcf0_bull = fcf0_base if fcf0_bull is None else fcf0_bull

    specs = [
        ("bear", wacc + wacc_stress, max(terminal_growth - tg_stress, 0.0), fcf0_bear),
        ("base", wacc, terminal_growth, fcf0_base),
        ("bull", max(wacc - wacc_stress, terminal_growth + tg_stress + 0.005),
         terminal_growth + tg_stress, fcf0_bull),
    ]
    cases: list[DcfCase] = []
    for label, w, tg, f0 in specs:
        g_imp, conv = solve_implied_growth(current_ev, f0, w, tg, years)
        fv = dcf_equity_value_per_share(f0, assumed_growth, w, tg, years, net_debt, shares)
        if current_price and not math.isnan(current_price) and current_price != 0:
            mos = (fv - current_price) / current_price
        else:
            mos = float("nan")
        cases.append(DcfCase(
            label=label, wacc=w, terminal_growth=tg, fcf0=f0,
            implied_growth=g_imp, implied_converged=conv,
            assumed_growth=assumed_growth, fair_value_per_share=fv,
            current_price=current_price, margin_of_safety=mos,
        ))
    return cases

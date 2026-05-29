"""Reverse-DCF unit tests against hand-computed values. Pure-number module —
no network, no fixtures. Offline-first per CLAUDE.md."""

from __future__ import annotations

import math

import pytest

from sycamore_prep.comps.reverse_dcf import (
    DcfCase,
    build_cases,
    dcf_enterprise_value,
    dcf_equity_value_per_share,
    solve_implied_growth,
)


def test_dcf_enterprise_value_flat_fcf_hand_computed():
    """fcf0=100, growth=0, wacc=10%, terminal=2%, 10y.
    Annuity of 100 @10% for 10y = 100 * (1 - 1.1^-10)/0.10 = 614.4567.
    TV = 100*1.02/(0.10-0.02) = 1275, discounted /1.1^10 = 491.5696.
    EV = 1106.026."""
    ev = dcf_enterprise_value(100.0, 0.0, 0.10, 0.02, 10)
    assert ev == pytest.approx(1106.026, rel=1e-4)


def test_dcf_enterprise_value_is_monotonic_increasing_in_growth():
    base = dict(fcf0=100.0, wacc=0.10, terminal_growth=0.02, years=10)
    lo = dcf_enterprise_value(growth=0.00, **base)
    mid = dcf_enterprise_value(growth=0.05, **base)
    hi = dcf_enterprise_value(growth=0.10, **base)
    assert lo < mid < hi


def test_dcf_requires_wacc_above_terminal_growth():
    with pytest.raises(ValueError):
        dcf_enterprise_value(100.0, 0.03, 0.02, 0.02, 10)
    with pytest.raises(ValueError):
        dcf_enterprise_value(100.0, 0.03, 0.01, 0.02, 10)


def test_solver_round_trips_a_known_growth():
    """Build EV from a known growth, then recover that growth by solving."""
    g_true = 0.06
    ev = dcf_enterprise_value(120.0, g_true, 0.09, 0.025, 10)
    g, converged = solve_implied_growth(ev, 120.0, 0.09, 0.025, 10)
    assert converged
    assert g == pytest.approx(g_true, abs=1e-6)


def test_solver_flags_target_above_bracket():
    g, converged = solve_implied_growth(1e15, 100.0, 0.09, 0.025, 10)
    assert not converged
    assert g == 1.00  # returns the hi edge, no silent clamp


def test_solver_flags_target_below_bracket():
    # A trivially small EV is below even the lowest-growth model value.
    g, converged = solve_implied_growth(1.0, 100.0, 0.09, 0.025, 10)
    assert not converged
    assert g == -0.50  # returns the lo edge


def test_solver_undefined_on_nonpositive_base_fcf():
    g, converged = solve_implied_growth(1000.0, 0.0, 0.09, 0.025, 10)
    assert math.isnan(g) and not converged
    g, converged = solve_implied_growth(1000.0, -50.0, 0.09, 0.025, 10)
    assert math.isnan(g) and not converged


def test_equity_value_per_share_subtracts_net_debt():
    ev = dcf_enterprise_value(50.0, 0.04, 0.09, 0.025, 10)
    per_share = dcf_equity_value_per_share(50.0, 0.04, 0.09, 0.025, 10, net_debt=200.0, shares=100.0)
    assert per_share == pytest.approx((ev - 200.0) / 100.0, rel=1e-9)


def test_build_cases_orders_bear_base_bull_and_surfaces_downside():
    cases = build_cases(
        current_market_cap=1000.0, net_debt=200.0, shares=100.0,
        fcf0_base=50.0, wacc=0.09, terminal_growth=0.025, years=10,
        assumed_growth=0.03,
    )
    assert [c.label for c in cases] == ["bear", "base", "bull"]
    bear, base, bull = cases
    # Bear stresses discounting up and terminal growth down.
    assert bear.wacc > base.wacc > bull.wacc
    assert bear.terminal_growth < base.terminal_growth < bull.terminal_growth
    # Same EV target + same fcf0 → bear must demand the HIGHEST implied growth.
    assert bear.implied_growth > base.implied_growth > bull.implied_growth
    # Margin of safety computed against the same current price.
    assert all(c.current_price == pytest.approx(10.0) for c in cases)
    for c in cases:
        assert c.margin_of_safety == pytest.approx((c.fair_value_per_share - 10.0) / 10.0)


def test_build_cases_uses_supplied_current_price_for_margin_of_safety():
    # market_cap / shares = 10, but the real quote is 12 → MoS measured vs 12.
    cases = build_cases(
        current_market_cap=1000.0, net_debt=0.0, shares=100.0,
        fcf0_base=50.0, wacc=0.09, terminal_growth=0.025, years=10,
        assumed_growth=0.03, current_price=12.0,
    )
    for c in cases:
        assert c.current_price == pytest.approx(12.0)
        assert c.margin_of_safety == pytest.approx((c.fair_value_per_share - 12.0) / 12.0)


def test_build_cases_bear_uses_conservative_fcf_seed():
    cases = build_cases(
        current_market_cap=1000.0, net_debt=0.0, shares=100.0,
        fcf0_base=60.0, wacc=0.09, terminal_growth=0.025, years=10,
        assumed_growth=0.03, fcf0_bear=40.0, fcf0_bull=70.0,
    )
    bear, base, bull = cases
    assert bear.fcf0 == 40.0 and base.fcf0 == 60.0 and bull.fcf0 == 70.0

"""Volatility overlay metrics — a DOWNSIDE cross-check, not a trading signal.

Sycamore's first principle is limiting permanent loss of capital. These
functions turn raw options-vol observations into the downside-first read an
analyst wants *beside* a value thesis — never folded into the three-attribute
score, only used to annotate it:

  * iv_rank_pct / iv_percentile_pct — how stressed the option market is on this
    name vs. its own trailing year (0-100; high = market pricing more risk).
  * expected_move_pct / sigma_down_price — the option-implied 1-sigma move over
    a horizon. We surface the DOWNSIDE leg (price - move).
  * expected_move_into_earnings — implied 1-sigma move through the next print,
    using the term-structure IV of the first expiration after the report date.
  * put_skew — 25-delta put IV minus 25-delta call IV. Positive = the market is
    paying up for downside protection (a pure downside signal). The math is
    tested here; the live per-strike feed is a documented Tier-2 follow-up
    (market-metrics gives the IV term structure, not per-strike Greeks).
  * implied_downside_breaches_mos — does the option-implied 1-sigma-down price
    already sit below the analyst's margin-of-safety floor? Ties vol directly
    to the fundamental downside case (and to the reverse-DCF floor in Phase 3).

All formulas are documented inline so every number is auditable per CLAUDE.md.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Mapping

from ..adapters.base import VolatilityFrame


# Annualization convention: tastytrade's IV index is an annualized vol quoted
# on a 365 calendar-day basis, so the 1-sigma move over `days` calendar days is
# iv * sqrt(days / 365). Stated here so the expected-move number is auditable.
DAYS_PER_YEAR = 365.0

# Informational vol-flag thresholds. These NEVER touch the screener score or
# rank — they only annotate (CLAUDE.md principle 2: decompose, never hide;
# principle 1: surface downside at least as prominently as upside).
ELEVATED_IV_RANK = 50.0       # IV rank >= this -> market pricing elevated risk
HIGH_IV_RANK = 80.0           # IV rank >= this -> market pricing high risk
EARNINGS_IMMINENT_DAYS = 14   # earnings within this many days -> event risk
THIN_LIQUIDITY_RATING = 2.0   # liquidity-rating 1(thin)..4(deep); <= this = thin


# ---------------------------------------------------------------------------
# Low-level pure math (float in, float out) — directly fixture-tested.
# ---------------------------------------------------------------------------
def expected_move_pct(iv: float | None, days: float | None) -> float | None:
    """Option-implied 1-sigma move as a fraction of spot over `days`.

    em% = iv * sqrt(days / 365). iv is a decimal (0.30 = 30% annualized).
    """
    if iv is None or days is None or iv < 0 or days < 0:
        return None
    return float(iv) * math.sqrt(float(days) / DAYS_PER_YEAR)


def expected_move_price(price: float | None, iv: float | None, days: float | None) -> float | None:
    """Dollar 1-sigma move = price * expected_move_pct."""
    em = expected_move_pct(iv, days)
    if em is None or price is None:
        return None
    return float(price) * em


def sigma_down_price(price: float | None, iv: float | None, days: float | None) -> float | None:
    """The DOWNSIDE leg: price * (1 - expected_move_pct). The number a
    downside-first analyst cares about — where a 1-sigma adverse move lands."""
    em = expected_move_pct(iv, days)
    if em is None or price is None:
        return None
    return float(price) * (1.0 - em)


def put_skew(iv_by_delta: Mapping[float, float], wing: float = 0.25) -> float | None:
    """25-delta put IV minus 25-delta call IV.

    `iv_by_delta` maps SIGNED option delta -> implied vol (puts negative, calls
    positive). Picks the strike whose |delta| is closest to `wing` on each side.
    Positive result = the market pays up for downside protection (skew toward
    puts) — a pure downside signal. Returns None if either wing is missing.
    """
    put_iv = _closest_wing(iv_by_delta, wing, want_puts=True)
    call_iv = _closest_wing(iv_by_delta, wing, want_puts=False)
    if put_iv is None or call_iv is None:
        return None
    return float(put_iv - call_iv)


def _closest_wing(iv_by_delta: Mapping[float, float], wing: float, want_puts: bool) -> float | None:
    best_iv = None
    best_dist = None
    for delta, iv in iv_by_delta.items():
        if want_puts and delta >= 0:
            continue
        if not want_puts and delta <= 0:
            continue
        dist = abs(abs(delta) - abs(wing))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_iv = iv
    return None if best_iv is None else float(best_iv)


def implied_downside_breaches_mos(
    price: float | None,
    iv: float | None,
    days: float | None,
    mos_floor_price: float | None,
) -> dict | None:
    """Does the option-implied 1-sigma-down price puncture the analyst's
    margin-of-safety floor?

    `mos_floor_price` is the downside price the analyst believes is protected
    (a conservative fair value / hard floor; in Phase 3 this is the reverse-DCF
    downside). Returns the comparison or None if inputs are missing.
    """
    sd = sigma_down_price(price, iv, days)
    if sd is None or mos_floor_price is None:
        return None
    floor = float(mos_floor_price)
    return {
        "sigma_down_price": sd,
        "mos_floor": floor,
        "breaches": sd < floor,
        # cushion of the implied-down price over the floor; negative = breach.
        "cushion_pct": (sd / floor - 1.0) if floor else None,
    }


# ---------------------------------------------------------------------------
# Frame readers — pull canonical metrics out of a VolatilityFrame.
# ---------------------------------------------------------------------------
def _coerce_date(v) -> date | None:
    if v is None or isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def iv_index(vf: VolatilityFrame, ticker: str) -> float | None:
    return vf.latest("iv_index", ticker)


def iv_rank_pct(vf: VolatilityFrame, ticker: str) -> float | None:
    """IV rank as 0-100 (provider reports 0-1)."""
    r = vf.latest("iv_rank", ticker)
    return None if r is None else r * 100.0


def iv_percentile_pct(vf: VolatilityFrame, ticker: str) -> float | None:
    """IV percentile as 0-100 (provider reports 0-1)."""
    r = vf.latest("iv_percentile", ticker)
    return None if r is None else r * 100.0


def beta(vf: VolatilityFrame, ticker: str) -> float | None:
    return vf.latest("beta", ticker)


def liquidity_rating(vf: VolatilityFrame, ticker: str) -> float | None:
    return vf.latest("liquidity_rating", ticker)


def next_earnings_date(vf: VolatilityFrame, ticker: str) -> str | None:
    sub = vf.metric("next_earnings", ticker=ticker)
    if sub.empty:
        return None
    detail = sub.iloc[-1]["detail"]
    d = _coerce_date(detail)
    return d.isoformat() if d else None


def days_to(target_date, asof=None) -> int | None:
    target = _coerce_date(target_date)
    if target is None:
        return None
    base = _coerce_date(asof) or date.today()
    return (target - base).days


def iv_for_horizon(vf: VolatilityFrame, ticker: str, target_days: float, asof=None) -> float | None:
    """IV of the expiration whose days-to-expiry is closest to `target_days`.

    Falls back to the IV index when no term structure is available.
    """
    base = _coerce_date(asof) or date.today()
    sub = vf.metric("iv_expiration", ticker=ticker)
    best_iv = None
    best_dist = None
    for _, r in sub.iterrows():
        exp = _coerce_date(r["detail"])
        iv = r["value"]
        if exp is None or iv is None:
            continue
        dte = (exp - base).days
        if dte < 0:
            continue
        dist = abs(dte - target_days)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_iv = float(iv)
    return best_iv if best_iv is not None else iv_index(vf, ticker)


def expected_move_into_earnings(vf: VolatilityFrame, ticker: str, price=None, asof=None) -> dict | None:
    """Implied 1-sigma move through the next earnings print.

    Uses the IV of the first expiration on/after the expected report date over
    that expiration's days-to-expiry (IV and horizon from the same expiration).
    Falls back to the IV index over days-to-earnings if no post-earnings
    expiration is present.
    """
    edate = next_earnings_date(vf, ticker)
    e = _coerce_date(edate)
    if e is None:
        return None
    base = _coerce_date(asof) or date.today()

    chosen_iv = None
    chosen_dte = None
    for _, r in vf.metric("iv_expiration", ticker=ticker).iterrows():
        exp = _coerce_date(r["detail"])
        iv = r["value"]
        if exp is None or iv is None or exp < e:
            continue
        dte = (exp - base).days
        if chosen_dte is None or dte < chosen_dte:
            chosen_iv = float(iv)
            chosen_dte = dte

    basis = "post_earnings_expiration"
    if chosen_iv is None:
        chosen_iv = iv_index(vf, ticker)
        chosen_dte = (e - base).days
        basis = "iv_index_fallback"
    if chosen_iv is None:
        return None

    return {
        "iv": chosen_iv,
        "days": chosen_dte,
        "em_pct": expected_move_pct(chosen_iv, chosen_dte),
        "downside_price": sigma_down_price(price, chosen_iv, chosen_dte) if price is not None else None,
        "earnings_date": edate,
        "basis": basis,
    }


# ---------------------------------------------------------------------------
# Assembler — the annotation block the screener / CLI consume.
# ---------------------------------------------------------------------------
def volatility_overlay(
    vf: VolatilityFrame,
    ticker: str,
    price: float | None = None,
    mos_floor: float | None = None,
    horizon_days: int = 30,
    asof=None,
) -> dict:
    """Build the downside-first vol annotation for one ticker.

    `price` and `mos_floor` are optional — the price-free metrics (IV rank,
    percentile, expected-move %) populate without them; the dollar downside and
    the margin-of-safety cross-check populate only when both are supplied.
    """
    ivx = iv_index(vf, ticker)
    ivr = iv_rank_pct(vf, ticker)
    ivp = iv_percentile_pct(vf, ticker)
    b = beta(vf, ticker)
    liq = liquidity_rating(vf, ticker)
    edate = next_earnings_date(vf, ticker)
    dte_earn = days_to(edate, asof) if edate else None

    # 30d expected move uses the IV index directly (it IS the ~30d annualized
    # vol), keeping IV and horizon internally consistent.
    em_30 = expected_move_pct(ivx, horizon_days)
    earn = expected_move_into_earnings(vf, ticker, price=price, asof=asof)
    em_earn = earn["em_pct"] if earn else None

    flags: list[str] = []
    if ivr is not None and ivr >= HIGH_IV_RANK:
        flags.append("high_iv_rank")
    elif ivr is not None and ivr >= ELEVATED_IV_RANK:
        flags.append("elevated_iv_rank")
    if dte_earn is not None and 0 <= dte_earn <= EARNINGS_IMMINENT_DAYS:
        flags.append("earnings_imminent")
    if liq is not None and liq <= THIN_LIQUIDITY_RATING:
        flags.append("thin_liquidity")

    mos = None
    if price is not None and mos_floor is not None and ivx is not None:
        mos = implied_downside_breaches_mos(price, ivx, horizon_days, mos_floor)
        if mos and mos.get("breaches"):
            flags.append("implied_downside_breaches_mos")

    return {
        "ticker": ticker.upper(),
        "iv_index": ivx,
        "iv_rank": ivr,
        "iv_percentile": ivp,
        "vol_beta": b,
        "liquidity_rating": liq,
        "expected_move_30d_pct": em_30,
        "expected_move_earnings_pct": em_earn,
        "days_to_earnings": dte_earn,
        "next_earnings_date": edate,
        "sigma_down_30d_price": sigma_down_price(price, ivx, horizon_days),
        "mos_cross_check": mos,
        "vol_flags": flags,
        "vol_source": "tastytrade" if (ivx is not None or ivr is not None) else "",
    }

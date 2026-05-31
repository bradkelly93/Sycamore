"""Projection helpers that split the NON-PRIMARY overlay panels out of an engine
frame and mark them ``non_primary``. Single home for the §3.4 segregation so both
front-ends render the wall-off identically.
"""

from __future__ import annotations

from .serde import clean_scalar, split_flags
from .viewmodels import TASTYTRADE, VolPanelView, fig

# Vol overlay columns the screener appends (kept near the scores per screen.py).
VOL_COLUMNS = [
    "iv_rank", "iv_percentile", "iv_index", "iv_hv_30_day_diff",
    "expected_move_30d_pct", "expected_move_earnings_pct", "days_to_earnings",
    "next_earnings_date", "sigma_down_30d_price", "put_skew_25d",
    "vol_beta", "liquidity_rating", "vol_flags", "vol_source",
]


def screener_overlay_columns(columns) -> dict[str, list[str]]:
    """Classify a screener frame's columns into the three overlay groups (the rest
    are the primary fundamental columns)."""
    cols = list(columns)
    vol = [c for c in cols if c in VOL_COLUMNS]
    tv = [c for c in cols if c == "passes_screen" or c.startswith("tv_")]
    prediction = [c for c in cols if c.startswith("event_")]
    return {"vol": vol, "tv": tv, "prediction": prediction}


def _ge(v, threshold: float) -> bool:
    try:
        return v is not None and float(v) >= threshold
    except (TypeError, ValueError):
        return False


def _clean_mos(m) -> dict | None:
    if not m:
        return None
    return {k: clean_scalar(v) for k, v in m.items()}


def vol_panel_from_overlay(d: dict | None, *, note: str | None = None) -> VolPanelView | None:
    """Build the downside-first vol panel from ``metrics.volatility.volatility_overlay``'s
    dict. ``mos_cross_check.breaches`` flags the implied-downside risk."""
    if d is None:
        return VolPanelView(note=note) if note else None
    mos = _clean_mos(d.get("mos_cross_check"))
    breach = bool(mos and mos.get("breaches"))
    return VolPanelView(
        iv_rank=fig(d.get("iv_rank"), TASTYTRADE, flag="warn" if _ge(d.get("iv_rank"), 50) else None),
        iv_percentile=fig(d.get("iv_percentile"), TASTYTRADE),
        iv_index=fig(d.get("iv_index"), TASTYTRADE, fmt="pct"),
        expected_move_30d_pct=fig(d.get("expected_move_30d_pct"), TASTYTRADE, fmt="pct"),
        expected_move_earnings_pct=fig(d.get("expected_move_earnings_pct"), TASTYTRADE, fmt="pct"),
        days_to_earnings=fig(d.get("days_to_earnings"), TASTYTRADE, fmt="int"),
        next_earnings_date=clean_scalar(d.get("next_earnings_date")),
        vol_beta=fig(d.get("vol_beta"), TASTYTRADE),
        liquidity_rating=fig(d.get("liquidity_rating"), TASTYTRADE),
        sigma_down_30d_price=fig(d.get("sigma_down_30d_price"), TASTYTRADE, fmt="ccy",
                                 flag="risk" if breach else None),
        mos_cross_check=mos,
        vol_flags=split_flags(d.get("vol_flags")),
        note=note,
    )

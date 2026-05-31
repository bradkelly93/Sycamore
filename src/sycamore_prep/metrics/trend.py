"""Transparent trend-regime classification from a daily price series.

NON-PRIMARY context for the technical overlay (CLAUDE.md: bottom-up,
downside-first) — it never enters the fundamental score. The regime is a simple,
auditable vote over moving-average structure so the analyst can defend every
input:
  - last close above the slow SMA (200d),
  - fast SMA (50d) above the slow SMA (golden-cross state),
  - slow SMA sloping up over the last ~month.
3 votes -> Bull, 0 -> Bear, otherwise Neutral. The raw drivers are surfaced too
so the label is never a black box.
"""

from __future__ import annotations

import pandas as pd


def _classify(above_slow: bool, golden: bool, slope_up: bool) -> str:
    votes = int(above_slow) + int(golden) + int(slope_up)
    if votes == 3:
        return "Bull"
    if votes == 0:
        return "Bear"
    return "Neutral"


def trend_regime(
    prices: pd.DataFrame,
    fast: int = 50,
    slow: int = 200,
    slope_lookback: int = 21,
) -> dict:
    """Classify the latest trend regime from a daily OHLC(V) frame with a
    `close` column (and `date` if available).

    Returns {regime, pct_above_200, slope200_pct, asof}. Needs at least
    `slow + slope_lookback` bars; otherwise regime is 'insufficient_history'.
    """
    px = prices.copy()
    if "date" in px.columns:
        px = px.sort_values("date")
    asof = str(px["date"].iloc[-1]) if ("date" in px.columns and len(px)) else None
    close = pd.to_numeric(px["close"], errors="coerce").dropna().reset_index(drop=True)

    if len(close) < slow + slope_lookback:
        return {"regime": "insufficient_history", "pct_above_200": None,
                "slope200_pct": None, "asof": asof}

    sma_fast = close.rolling(fast).mean()
    sma_slow = close.rolling(slow).mean()
    last_close = float(close.iloc[-1])
    s_fast = float(sma_fast.iloc[-1])
    s_slow = float(sma_slow.iloc[-1])
    s_slow_prev = float(sma_slow.iloc[-1 - slope_lookback])

    regime = _classify(last_close > s_slow, s_fast > s_slow, s_slow > s_slow_prev)
    return {
        "regime": regime,
        "pct_above_200": round(last_close / s_slow - 1.0, 4),
        "slope200_pct": round(s_slow / s_slow_prev - 1.0, 4),
        "asof": asof,
    }

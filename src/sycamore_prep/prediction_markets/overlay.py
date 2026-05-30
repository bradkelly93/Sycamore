"""Build the prediction-market overlay frame from the confirmed mapping.

SEPARATE LENS (CLAUDE.md): nothing here ever feeds the screener composite,
comps, or fair value. For each mapped market we surface not just the implied
probability but its recent move (`prob_chg_30d`, the "positioning" signal) and
the liquidity behind it, plus a `read_through` classifying the market as
RISK / OPPORTUNITY / WATCH for the name's thesis. Downside read-throughs sort
to the top (downside-first). Every row stays tagged non-primary.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..adapters import cache as cache_mod
from ..adapters.base import EventProbabilityProvider
from ..config import AppConfig, load_config
from . import mapping as mapping_mod


OVERLAY_COLUMNS = [
    "ticker", "aperture", "event_type", "direction", "read_through",
    "question", "implied_prob", "prob_chg_30d", "liquidity", "volume",
    "resolution_date", "relevance_score", "confirmed", "slug", "as_of",
    "url", "source",
]

_RT_RANK = {"RISK": 0, "OPPORTUNITY": 1, "WATCH": 2}


def _to_float(v: object) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _first_prob(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    s = df["implied_prob"].dropna()
    return float(s.iloc[0]) if not s.empty else None


def _pick_outcome_row(market_df: pd.DataFrame) -> pd.Series | None:
    """From a market's per-outcome rows pick the most informative one: prefer
    'Yes', else the outcome carrying the highest implied probability."""
    if market_df is None or market_df.empty:
        return None
    yes = market_df[market_df["outcome"].astype(str).str.lower() == "yes"]
    if not yes.empty:
        return yes.iloc[0]
    return market_df.sort_values(
        "implied_prob", ascending=False, na_position="last"
    ).iloc[0]


def _prob_change(
    slug: str,
    outcome: object,
    current_prob: float | None,
    as_of_date: str,
    lookback_days: int = 30,
    tol_days: int = 14,
) -> float | None:
    """Current implied prob minus the closest cached snapshot ~`lookback_days`
    old (within `tol_days`). Needs a prior local snapshot — returns None if the
    history isn't there yet."""
    if current_prob is None:
        return None
    snaps = cache_mod.list_market_snapshots(slug)
    if not snaps:
        return None
    target = pd.Timestamp(as_of_date) - pd.Timedelta(days=lookback_days)
    best_path = None
    best_gap: int | None = None
    for snap_date, path in snaps:
        try:
            ts = pd.Timestamp(snap_date)
        except (ValueError, TypeError):
            continue
        if ts >= pd.Timestamp(as_of_date):  # must be strictly in the past
            continue
        gap = abs((ts - target).days)
        if gap <= tol_days and (best_gap is None or gap < best_gap):
            best_path, best_gap = path, gap
    if best_path is None:
        return None
    old = pd.read_parquet(best_path)
    if outcome is not None and "outcome" in old.columns:
        old = old[old["outcome"].astype(str).str.lower() == str(outcome).lower()]
    prior = _first_prob(old)
    if prior is None:
        return None
    return round(float(current_prob) - prior, 3)


def _read_through(direction: object, implied_prob: float | None,
                  contradiction_prob: float) -> str:
    if implied_prob is None:
        return "WATCH"
    d = str(direction or "").lower()
    if d == "risk" and implied_prob >= contradiction_prob:
        return "RISK"
    if d == "opportunity" and implied_prob >= contradiction_prob:
        return "OPPORTUNITY"
    return "WATCH"


def build_overlay(
    provider: EventProbabilityProvider,
    tickers: list[str] | None = None,
    cfg: AppConfig | None = None,
    *,
    mapping_path: Path | str | None = None,
    confirmed_only: bool = True,
    min_relevance: float = 0.0,
    include_macro: bool = False,
    downside_only: bool = False,
) -> pd.DataFrame:
    """Refresh implied probabilities for the mapped markets and assemble the
    overlay. `include_macro=False` drops the macro aperture so the bottom-up
    workflow stays clean by default."""
    cfg = cfg or load_config()
    m = mapping_mod.load_mapping(mapping_path)
    if m.empty:
        return pd.DataFrame(columns=OVERLAY_COLUMNS)

    if tickers:
        wanted = {t.upper() for t in tickers}
        m = m[m["ticker"].astype(str).str.upper().isin(wanted)]
    if confirmed_only:
        m = m[m["confirmed"] == True]  # noqa: E712 — pandas boolean mask
    if min_relevance:
        m = m[m["relevance_score"].fillna(0).astype(float) >= min_relevance]
    if not include_macro:
        m = m[m["aperture"].astype(str).str.lower() != "macro"]
    if m.empty:
        return pd.DataFrame(columns=OVERLAY_COLUMNS)

    contradiction_prob = cfg.prediction.contradiction_prob
    rows: list[dict] = []
    for _, r in m.iterrows():
        try:
            market_df = provider.get_market(str(r["slug"]))
        except Exception:  # noqa: BLE001 — network/egress-blocked; skip gracefully
            continue
        pick = _pick_outcome_row(market_df)
        if pick is None:
            continue
        as_of = str(pick.get("as_of") or "")
        as_of_date = as_of[:10] if as_of else str(date.today())
        prob = _to_float(pick.get("implied_prob"))
        rows.append({
            "ticker": r["ticker"],
            "aperture": r.get("aperture"),
            "event_type": r.get("event_type"),
            "direction": r.get("direction"),
            "read_through": _read_through(r.get("direction"), prob, contradiction_prob),
            "question": pick.get("question") or r.get("question"),
            "implied_prob": prob,
            "prob_chg_30d": _prob_change(str(r["slug"]), pick.get("outcome"), prob, as_of_date),
            "liquidity": _to_float(pick.get("liquidity")),
            "volume": _to_float(pick.get("volume")),
            "resolution_date": pick.get("resolution_date"),
            "relevance_score": _to_float(r.get("relevance_score")),
            "confirmed": bool(r.get("confirmed")),
            "slug": r["slug"],
            "as_of": as_of,
            "url": pick.get("url"),
            "source": pick.get("source"),
        })

    out = pd.DataFrame(rows, columns=OVERLAY_COLUMNS)
    if out.empty:
        return out
    if downside_only:
        out = out[out["read_through"] == "RISK"]
        if out.empty:
            return out
    # Downside-first ordering: RISK, then by implied prob, then liquidity.
    out = out.assign(_rt=out["read_through"].map(_RT_RANK).fillna(3))
    out = out.sort_values(
        ["_rt", "implied_prob", "liquidity"],
        ascending=[True, False, False], na_position="last",
    ).drop(columns="_rt")
    return out.reset_index(drop=True)


def screener_annotations(overlay_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the overlay to one row per ticker for joining onto the screener.

    Picks the single most material market per name (a RISK read-through if any,
    else the highest-probability market). Returns a frame indexed by ticker
    with `event_*` columns. These are ANNOTATIONS ONLY — never merged into the
    composite score (CLAUDE.md: decompose, never collapse)."""
    cols = [
        "event_top_event", "event_top_prob", "event_prob_chg_30d",
        "event_read_through", "event_resolution_date", "event_liquidity",
        "event_aperture", "event_source", "event_url",
    ]
    if overlay_df is None or overlay_df.empty:
        return pd.DataFrame(columns=cols)
    risk = overlay_df[overlay_df["read_through"] == "RISK"]
    pool = risk if not risk.empty else overlay_df
    pool = pool.sort_values(
        ["implied_prob", "liquidity"], ascending=False, na_position="last"
    )
    records: dict[str, dict] = {}
    for ticker, grp in pool.groupby("ticker"):
        top = grp.iloc[0]
        records[str(ticker)] = {
            "event_top_event": top["question"],
            "event_top_prob": top["implied_prob"],
            "event_prob_chg_30d": top["prob_chg_30d"],
            "event_read_through": top["read_through"],
            "event_resolution_date": top["resolution_date"],
            "event_liquidity": top["liquidity"],
            "event_aperture": top["aperture"],
            "event_source": top["source"],
            "event_url": top["url"],
        }
    return pd.DataFrame.from_dict(records, orient="index")[cols]


def write_overlay(df: pd.DataFrame, path: Path | str) -> Path:
    """Write the overlay to xlsx (styled) with a CSV sibling for auditability."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path.with_suffix(".csv"), index=False)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "overlay"
    cols = list(df.columns)
    for c, name in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="left")
    for r, (_, row) in enumerate(df.iterrows(), start=2):
        for c, col in enumerate(cols, start=1):
            v = row[col]
            if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
                v = None
            ws.cell(row=r, column=c, value=v)
    ws.freeze_panes = "A2"
    for i, col in enumerate(cols, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = (
            max(12, min(40, len(str(col)) + 4))
        )
    wb.save(path)
    return path

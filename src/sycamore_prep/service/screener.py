"""Service wrapper for the three-attribute screener.

Wraps ``screener.run_screener`` (no recompute) into a :class:`ScreenerView` that
keeps Q1/Q2/Q3 separate, tags every figure's source, segregates the NON-PRIMARY
overlay columns, and exposes the rank-unchanged proof. ``verify_rank_unchanged``
is the on-demand dual run that *demonstrates* (not just claims) that toggling the
overlays leaves ``composite_rank`` byte-identical.
"""

from __future__ import annotations

import pandas as pd

from ..config import cache_dir
from ..screener import run_screener
from .overlays import screener_overlay_columns
from .serde import clean_scalar, split_flags, split_sources
from .viewmodels import (
    EDGAR,
    YFINANCE,
    Figure,
    RankProof,
    RankUnchangedClaim,
    ScreenerRowView,
    ScreenerView,
    fig,
)

# Valuation multiples + the composite blend depend on (non-primary) price, so they
# are labelled as a primary/non-primary splice (CLAUDE.md principle 3). Pure-EDGAR
# quality + improving metrics stay primary.
MIXED = "edgar + yfinance price (non-primary)"

_QUALITY_RAW = {"roic", "rotce", "gross_margin", "gross_margin_stability",
                "fcf_margin", "interest_coverage", "net_debt_ebitda"}
_IMPROVING_RAW = {"rev_3yr_cagr", "eps_3yr_cagr", "fcf_3yr_cagr", "rev_growth_acceleration"}
_VALUATION_RAW = {"pe", "ev_ebitda", "fcf_yield", "p_tbv", "fcf_yield_pctile_own_history"}

_IDENTITY = {"name", "gics_sector", "is_bank", "market_cap", "sources", "error",
             "negative_space", "ns_flags", "composite_rank", "composite_score",
             "q1_quality_score", "q2_valuation_score", "q3_improving_score"}

_FMT = {
    "market_cap": "ccy", "iv_index": "pct", "expected_move_30d_pct": "pct",
    "expected_move_earnings_pct": "pct", "days_to_earnings": "int",
    "pe": "mult", "ev_ebitda": "mult", "p_tbv": "mult", "fcf_yield": "pct",
    "roic": "pct", "rotce": "pct", "gross_margin": "pct", "fcf_margin": "pct",
    "rev_3yr_cagr": "pct", "eps_3yr_cagr": "pct", "fcf_3yr_cagr": "pct",
    "net_debt_ebitda": "mult", "interest_coverage": "mult",
}


def _fmt(col: str) -> str:
    return _FMT.get(col, "raw")


def _component_source(col: str) -> str:
    if col in _VALUATION_RAW:
        return MIXED
    return EDGAR


def _row_view(tk: str, row: pd.Series, overlay_cols: dict[str, list[str]]) -> ScreenerRowView:
    neg = bool(clean_scalar(row.get("negative_space")) or False)
    rank = clean_scalar(row.get("composite_rank"))

    raw_components: dict[str, Figure] = {}
    for col in row.index:
        if col in _IDENTITY or col in overlay_cols["vol"] or col in overlay_cols["tv"] \
                or col in overlay_cols["prediction"] or col.endswith("_pctile"):
            continue
        if col in _QUALITY_RAW or col in _IMPROVING_RAW or col in _VALUATION_RAW:
            raw_components[col] = fig(row.get(col), _component_source(col), fmt=_fmt(col))

    vol_cells = {c: fig(row.get(c), "tastytrade (non-primary)", fmt=_fmt(c))
                 for c in overlay_cols["vol"] if c not in ("vol_flags", "vol_source")}
    tv_cells = {c: clean_scalar(row.get(c)) for c in overlay_cols["tv"]}
    pred_cells = {c: clean_scalar(row.get(c)) for c in overlay_cols["prediction"]}

    return ScreenerRowView(
        ticker=tk,
        name=clean_scalar(row.get("name")),
        gics_sector=clean_scalar(row.get("gics_sector")),
        is_bank=bool(clean_scalar(row.get("is_bank")) or False),
        market_cap=fig(row.get("market_cap"), YFINANCE, fmt="ccy"),
        q1_quality=fig(row.get("q1_quality_score"), EDGAR),
        q2_valuation=fig(row.get("q2_valuation_score"), MIXED),
        q3_improving=fig(row.get("q3_improving_score"), EDGAR),
        composite_score=fig(row.get("composite_score"), MIXED),
        composite_rank=int(rank) if rank is not None else None,
        negative_space=neg,
        ns_flags=split_flags(row.get("ns_flags")),
        flag="risk" if neg else None,
        raw_components=raw_components,
        vol=vol_cells or None,
        tv=tv_cells or None,
        prediction=pred_cells or None,
        sources=split_sources(row.get("sources")),
        error=clean_scalar(row.get("error")),
    )


def _to_view(df: pd.DataFrame, *, tickers, sector, limit,
             with_vol, tv_overlay, with_prediction_overlay) -> ScreenerView:
    overlay_cols = screener_overlay_columns(df.columns)
    rows = [_row_view(tk, df.loc[tk], overlay_cols) for tk in df.index]
    src_summary = sorted({s for r in rows for s in r.sources})
    return ScreenerView(
        tickers_requested=list(tickers) if tickers else None,
        sector=sector,
        limit=limit,
        rows=rows,
        column_order=["ticker"] + list(df.columns),
        overlay_columns=overlay_cols,
        overlays_active={"vol": with_vol, "tv": tv_overlay, "prediction": with_prediction_overlay},
        rank_unchanged=RankUnchangedClaim(),
        notes={
            "vol": df.attrs.get("vol_note"),
            "tv": df.attrs.get("tv_note"),
            "prediction": df.attrs.get("prediction_note"),
        },
        source_summary=src_summary,
    )


def run(tickers=None, sector=None, limit=None, *, with_vol=False, tv_overlay=False,
        with_prediction_overlay=False, hard_exclude_neg_space=False,
        skip_market_cap=False, refresh_tv=False) -> ScreenerView:
    out_path = cache_dir() / "screener_output.xlsx"
    df = run_screener(
        tickers=tickers, sector=sector, limit=limit, output_path=out_path,
        skip_market_cap=skip_market_cap, hard_exclude_neg_space=hard_exclude_neg_space,
        with_vol=with_vol, tv_overlay=tv_overlay, refresh_tv=refresh_tv,
        with_prediction_overlay=with_prediction_overlay,
    )
    view = _to_view(df, tickers=tickers, sector=sector, limit=limit,
                    with_vol=with_vol, tv_overlay=tv_overlay,
                    with_prediction_overlay=with_prediction_overlay)
    if out_path.exists():   # full ranked xlsx is downloadable (auditability §3.6)
        from . import artifacts
        view.screener_artifact = artifacts.register(out_path, "xlsx")
    return view


def verify_rank_unchanged(tickers=None, sector=None, limit=None, *,
                          hard_exclude_neg_space=False, skip_market_cap=False,
                          with_vol=True, tv_overlay=True, with_prediction_overlay=True) -> RankProof:
    """Dual run: screen overlays-OFF vs overlays-ON on the identical ticker set,
    then compare ``composite_rank`` ticker-by-ticker. Structurally always identical
    (overlays are spliced after ranking); the value is that the user *sees* it, and
    a regression would surface as ``identical=False``."""
    common = dict(tickers=tickers, sector=sector, limit=limit,
                  hard_exclude_neg_space=hard_exclude_neg_space, skip_market_cap=skip_market_cap)
    off = run_screener(**common, with_vol=False, tv_overlay=False, with_prediction_overlay=False,
                       output_path=cache_dir() / "_rankproof_off.xlsx")
    on = run_screener(**common, with_vol=with_vol, tv_overlay=tv_overlay,
                      with_prediction_overlay=with_prediction_overlay,
                      output_path=cache_dir() / "_rankproof_on.xlsx")

    a, b = off["composite_rank"], on["composite_rank"]
    identical = True
    rows = []
    for tk in a.index.union(b.index):
        ra = a.get(tk)
        rb = b.get(tk)
        ra_c = None if (ra is None or pd.isna(ra)) else int(ra)
        rb_c = None if (rb is None or pd.isna(rb)) else int(rb)
        if ra_c != rb_c:
            identical = False
        rows.append({"ticker": tk, "rank_overlays_off": ra_c, "rank_overlays_on": rb_c})

    checked = [n for n, active in (("vol", with_vol), ("tv", tv_overlay),
                                   ("prediction", with_prediction_overlay)) if active]
    skip_notes = [on.attrs.get(k) for k in ("vol_note", "tv_note", "prediction_note")]
    note = "; ".join(n for n in skip_notes if n) or None
    return RankProof(identical=identical, rows=rows, overlays_checked=checked, note=note)

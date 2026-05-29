"""Main screener entry point.

For each candidate ticker:
  1. Load cached EDGAR fundamentals (no network).
  2. Optionally fetch current market cap from yfinance (non-primary, tagged).
  3. Compute raw Quality / Valuation / Improving-Fundamentals components.
  4. Apply negative-space filters (Sycamore's revealed style).
  5. Cross-sectionally rank into sub-scores + downside-tilted composite.
  6. Write screener_output.xlsx with sub-scores reported separately.

Network-resilient: if yfinance fails for a ticker, valuation columns are
left blank and the ticker is still included with quality + improving scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from ..adapters import EdgarProvider, YFinanceProvider
from ..adapters.base import FinancialsFrame
from ..config import cache_dir, load_config
from ..metrics import (
    allowance_to_loans_fy,
    ebitda_fy,
    fcf_margin_fy,
    fcf_yield_fy,
    free_cash_flow_fy,
    gross_margin_fy,
    gross_margin_stability,
    interest_coverage_fy,
    is_bank,
    net_debt_to_ebitda_fy,
    net_interest_margin_fy,
    p_tbv_fy,
    pe_fy,
    percentile_vs_history,
    revenue_fy,
    roic_fy,
    roe_fy,
    rotce_fy,
    tangible_book_value_fy,
    ev_ebitda_fy,
)
from ..metrics.cashflow import fcf_conversion_fy
from ..metrics.profitability import _series, net_income_fy
from ..universe.builder import load_universe
from .scoring import score_universe


# Negative-space thresholds (Sycamore avoids these — exclude or down-rank).
NEG_SPACE_THRESHOLDS = {
    "extreme_pe": 40.0,
    "extreme_ev_ebitda": 25.0,
    "high_leverage_non_bank": 5.0,   # net debt / EBITDA
    "fcf_margin_floor": 0.02,
}


@dataclass
class ScreenerRow:
    ticker: str
    name: str
    gics_sector: str | None
    is_bank: bool
    market_cap: float | None
    # Quality components (raw)
    roic: float | None = None
    rotce: float | None = None
    gross_margin: float | None = None
    gross_margin_stability: float | None = None
    fcf_margin: float | None = None
    interest_coverage: float | None = None
    net_debt_ebitda: float | None = None
    # Valuation components (raw)
    pe: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None
    p_tbv: float | None = None
    fcf_yield_pctile_own_history: float | None = None
    # Improving-fundamentals components (raw)
    rev_3yr_cagr: float | None = None
    eps_3yr_cagr: float | None = None
    fcf_3yr_cagr: float | None = None
    rev_growth_acceleration: float | None = None
    # Negative-space flags
    ns_flags: list[str] = field(default_factory=list)
    # Sources contributing to this row
    sources: str = ""
    error: str | None = None


def _last(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.iloc[-1])


def _cagr(series: pd.Series, years: int) -> float | None:
    s = series.dropna()
    if len(s) < years + 1:
        return None
    start = s.iloc[-(years + 1)]
    end = s.iloc[-1]
    if start <= 0 or pd.isna(start) or pd.isna(end):
        return None
    return float((end / start) ** (1.0 / years) - 1.0)


def _growth_acceleration(series: pd.Series) -> float | None:
    """Last YoY growth minus prior YoY growth. Positive = accelerating."""
    s = series.dropna()
    if len(s) < 3:
        return None
    yoy_last = s.iloc[-1] / s.iloc[-2] - 1.0 if s.iloc[-2] > 0 else None
    yoy_prior = s.iloc[-2] / s.iloc[-3] - 1.0 if s.iloc[-3] > 0 else None
    if yoy_last is None or yoy_prior is None:
        return None
    return float(yoy_last - yoy_prior)


def _negative_space_flags(row: ScreenerRow) -> list[str]:
    flags = []
    if row.fcf_margin is not None and row.fcf_margin < NEG_SPACE_THRESHOLDS["fcf_margin_floor"]:
        flags.append("low_or_neg_fcf")
    if row.pe is not None and row.pe > NEG_SPACE_THRESHOLDS["extreme_pe"]:
        flags.append("extreme_pe")
    if row.ev_ebitda is not None and row.ev_ebitda > NEG_SPACE_THRESHOLDS["extreme_ev_ebitda"]:
        flags.append("extreme_ev_ebitda")
    if (not row.is_bank
        and row.net_debt_ebitda is not None
        and row.net_debt_ebitda > NEG_SPACE_THRESHOLDS["high_leverage_non_bank"]):
        flags.append("high_leverage")
    return flags


def _compute_raw_row(
    ticker: str,
    name: str,
    sector: str | None,
    ff: FinancialsFrame,
    market_cap: float | None,
) -> ScreenerRow:
    bank = is_bank(ff)
    row = ScreenerRow(
        ticker=ticker, name=name, gics_sector=sector,
        is_bank=bank, market_cap=market_cap,
    )
    sources = ["edgar (primary)"]

    # ---- Quality ----
    row.roic = _last(roic_fy(ff))
    row.rotce = _last(rotce_fy(ff)) if bank else None
    row.gross_margin = _last(gross_margin_fy(ff))
    row.gross_margin_stability = gross_margin_stability(ff)
    row.fcf_margin = _last(fcf_margin_fy(ff))
    row.interest_coverage = _last(interest_coverage_fy(ff))
    row.net_debt_ebitda = None if bank else _last(net_debt_to_ebitda_fy(ff))

    # ---- Valuation (requires market_cap) ----
    if market_cap is not None and market_cap > 0:
        row.pe = _last(pe_fy(ff, market_cap))
        row.ev_ebitda = None if bank else _last(ev_ebitda_fy(ff, market_cap))
        row.fcf_yield = _last(fcf_yield_fy(ff, market_cap))
        row.p_tbv = _last(p_tbv_fy(ff, market_cap)) if bank else None
        # "Percentile vs own history" headline: use current FCF yield as a
        # constant against the historical FCF-to-current-mcap series — proxy
        # until Phase 3 lines up historical prices for true bands.
        fcf_series = free_cash_flow_fy(ff)
        if not fcf_series.empty:
            hist_yields = fcf_series / market_cap
            current = row.fcf_yield
            row.fcf_yield_pctile_own_history = percentile_vs_history(
                hist_yields.iloc[:-1], current
            ) if current is not None else None
        sources.append("yfinance (non-primary)")

    # ---- Improving fundamentals ----
    rev = revenue_fy(ff)
    ni = net_income_fy(ff)
    fcf = free_cash_flow_fy(ff)
    row.rev_3yr_cagr = _cagr(rev, 3)
    row.eps_3yr_cagr = _cagr(ni, 3)
    row.fcf_3yr_cagr = _cagr(fcf, 3)
    row.rev_growth_acceleration = _growth_acceleration(rev)

    row.ns_flags = _negative_space_flags(row)
    row.sources = ", ".join(sources)
    return row


def _candidate_tickers(
    universe: pd.DataFrame | None,
    explicit: Iterable[str] | None,
    sector: str | None,
    limit: int | None,
) -> list[tuple[str, str, str | None]]:
    """Return [(ticker, name, sector)] tuples for the candidate set."""
    if explicit:
        return [(t.upper(), t.upper(), None) for t in explicit]
    if universe is None:
        cfg = load_config()
        return [(t, t, None) for t in cfg.test_tickers]
    df = universe.copy()
    if sector:
        # case-insensitive contains match on sector string
        mask = df["gics_sector"].fillna("").str.lower().str.contains(sector.lower())
        if sector.lower() == "banks":
            # also accept "Financials" (GICS sector for banks)
            mask = mask | df["gics_sector"].fillna("").str.lower().str.contains("financial")
        df = df[mask]
    if limit:
        df = df.head(limit)
    return [
        (str(r["ticker"]), str(r.get("name", r["ticker"])), r.get("gics_sector"))
        for _, r in df.iterrows()
    ]


def run_screener(
    tickers: Iterable[str] | None = None,
    sector: str | None = None,
    limit: int | None = None,
    output_path: Path | str | None = None,
    skip_market_cap: bool = False,
    hard_exclude_neg_space: bool = False,
) -> pd.DataFrame:
    """Run the three-attribute screen and write screener_output.xlsx.

    Negative-space handling (CLAUDE.md principle 2 — decompose, never hide):
    by default, names that trip a negative-space flag are KEPT in the output
    with all three sub-scores visible, just sorted to the bottom with their
    flags shown. Set hard_exclude_neg_space=True to drop them from the
    ranking entirely (composite_rank = NaN).
    """
    edgar = EdgarProvider()
    yfin = None if skip_market_cap else YFinanceProvider()

    universe = load_universe()
    candidates = _candidate_tickers(universe, tickers, sector, limit)
    if not candidates:
        raise ValueError(
            "No candidate tickers. Either pass tickers explicitly, "
            "run `build-universe` first, or set test_tickers in config.yaml."
        )

    rows: list[ScreenerRow] = []
    for ticker, name, sec in candidates:
        try:
            ff = edgar.get_financials(ticker)
        except Exception as exc:  # noqa: BLE001
            rows.append(ScreenerRow(
                ticker=ticker, name=name, gics_sector=sec, is_bank=False,
                market_cap=None, error=f"financials load failed: {exc}",
            ))
            continue
        mcap = None
        if yfin is not None:
            try:
                mcap = yfin.get_market_cap(ticker)
            except Exception:  # noqa: BLE001 — yfinance is intentionally flaky
                mcap = None
        rows.append(_compute_raw_row(ticker, name, sec, ff, mcap))

    raw = pd.DataFrame([r.__dict__ for r in rows]).set_index("ticker")
    # Stringify list column for xlsx output.
    raw["ns_flags"] = raw["ns_flags"].apply(
        lambda v: ", ".join(v) if isinstance(v, list) else ""
    )
    raw["negative_space"] = raw["ns_flags"].fillna("").str.len() > 0

    # Score ALL names — sub-scores stay honest even for flagged names so the
    # three-axis decomposition is never hidden (CLAUDE.md principle 2). The
    # negative-space policy only affects the RANK, not the sub-scores.
    result = score_universe(raw)
    score_cols = [c for c in result.df.columns
                  if c.endswith("_score") or c.endswith("_pctile")]
    out = raw.join(result.df[score_cols], how="left")

    if hard_exclude_neg_space:
        # Flagged names removed from the ranking entirely (rank = NaN), but
        # kept in the output with sub-scores + flags for transparency.
        clean = out.loc[~out["negative_space"], "composite_score"]
        out["composite_rank"] = clean.rank(ascending=False, method="min")
        out = out.sort_values(
            ["negative_space", "composite_score"],
            ascending=[True, False], na_position="last",
        )
    else:
        # Keep flagged names but down-rank: clean names first (by composite),
        # flagged names after (by their own composite). composite_score stays
        # the honest weighted blend — only the rank encodes the down-ranking.
        out = out.sort_values(
            ["negative_space", "composite_score"],
            ascending=[True, False], na_position="last",
        )
        out["composite_rank"] = range(1, len(out) + 1)
        out.loc[out["composite_score"].isna(), "composite_rank"] = np.nan

    # Reorder columns: identity, sub-scores prominent, then components, then sources.
    front = [
        "name", "gics_sector", "is_bank", "market_cap",
        "composite_rank", "composite_score",
        "q1_quality_score", "q2_valuation_score", "q3_improving_score",
        "negative_space", "ns_flags",
    ]
    front = [c for c in front if c in out.columns]
    rest = [c for c in out.columns if c not in front]
    out = out[front + rest]

    output_path = Path(output_path) if output_path else (cache_dir() / "screener_output.xlsx")
    _write_screener_xlsx(out, output_path)
    return out


def _write_screener_xlsx(df: pd.DataFrame, path: Path) -> None:
    """Single-sheet xlsx. Header row frozen, basic auto-width."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "screener"

    # Header
    cols = ["ticker"] + list(df.columns)
    for c, v in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=c, value=v)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="left")

    # Rows
    for r, (ticker, row) in enumerate(df.iterrows(), start=2):
        ws.cell(row=r, column=1, value=ticker)
        for c, col in enumerate(df.columns, start=2):
            v = row[col]
            if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
                v = None
            ws.cell(row=r, column=c, value=v)

    ws.freeze_panes = "B2"
    # Rough column-width pass.
    for i, col in enumerate(cols, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, min(28, len(str(col)) + 4))

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)

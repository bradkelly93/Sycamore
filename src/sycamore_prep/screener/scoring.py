"""Cross-sectional ranking + composite scoring.

Each attribute sub-score is the equal-weighted average of its components,
where each component is converted to a 0–100 cross-sectional percentile
inside the candidate set. Direction is set per-component (higher-is-better
vs. lower-is-better). NaN components are skipped in the average.

The composite is a downside-tilted blend (Quality 40 / Valuation 40 /
Improving 20) — Sycamore's first principle is limiting permanent loss, so
Quality + Valuation outweigh Growth. Sub-scores are still reported
separately in the output xlsx.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# Component direction: "higher" = higher raw value scores better.
COMPONENT_DIRECTION = {
    # Quality
    "roic": "higher",
    "rotce": "higher",
    "gross_margin": "higher",
    "fcf_margin": "higher",
    "interest_coverage": "higher",
    "net_debt_ebitda": "lower",
    # Valuation
    "fcf_yield": "higher",
    "fcf_yield_pctile_own_history": "higher",
    "pe": "lower",
    "ev_ebitda": "lower",
    "p_tbv": "lower",
    # Improving fundamentals
    "rev_3yr_cagr": "higher",
    "eps_3yr_cagr": "higher",
    "fcf_3yr_cagr": "higher",
    "rev_growth_acceleration": "higher",
}


# Composite weights — Quality + Valuation > Improving (downside-first).
ATTRIBUTE_WEIGHTS = {"q1": 0.40, "q2": 0.40, "q3": 0.20}


def _percentile_rank(series: pd.Series, direction: str) -> pd.Series:
    """Map raw values to 0–100 cross-sectional percentile.
    direction='higher' → higher raw value → higher score.
    direction='lower'  → lower raw value → higher score.
    """
    if series.dropna().empty:
        return pd.Series(np.nan, index=series.index)
    ranks = series.rank(method="average", pct=True) * 100.0
    if direction == "lower":
        ranks = 100.0 - ranks
    return ranks


def score_attribute(
    df: pd.DataFrame,
    components: list[str],
    out_prefix: str,
) -> tuple[pd.DataFrame, pd.Series]:
    """Add per-component percentile columns + return (df, attribute_score).

    Returns the score as a Series aligned with df.index. Components that are
    fully NaN across the candidate set are skipped (don't pull the average
    around).
    """
    score_cols: list[pd.Series] = []
    work = df.copy()
    for c in components:
        if c not in work.columns:
            continue
        direction = COMPONENT_DIRECTION.get(c, "higher")
        col = work[c].astype(float)
        ranked = _percentile_rank(col, direction)
        work[f"{out_prefix}_{c}_pctile"] = ranked
        if ranked.notna().any():
            score_cols.append(ranked)
    if not score_cols:
        return work, pd.Series(np.nan, index=df.index)
    attribute_score = pd.concat(score_cols, axis=1).mean(axis=1, skipna=True)
    return work, attribute_score


@dataclass
class CompositeResult:
    df: pd.DataFrame  # full screener output incl. attribute scores + percentile cols
    q1_col: str = "q1_quality_score"
    q2_col: str = "q2_valuation_score"
    q3_col: str = "q3_improving_score"
    composite_col: str = "composite_score"
    rank_col: str = "composite_rank"


def score_universe(raw: pd.DataFrame) -> CompositeResult:
    """Compute Q1/Q2/Q3 sub-scores + a downside-tilted composite rank.

    Input is a DataFrame indexed by ticker with the per-component RAW columns
    populated where available. Missing components are tolerated.
    """
    df = raw.copy()

    df, q1 = score_attribute(df, [
        "roic", "rotce", "gross_margin", "fcf_margin",
        "interest_coverage", "net_debt_ebitda",
    ], out_prefix="q1")
    df["q1_quality_score"] = q1

    df, q2 = score_attribute(df, [
        "fcf_yield", "fcf_yield_pctile_own_history",
        "pe", "ev_ebitda", "p_tbv",
    ], out_prefix="q2")
    df["q2_valuation_score"] = q2

    df, q3 = score_attribute(df, [
        "rev_3yr_cagr", "eps_3yr_cagr", "fcf_3yr_cagr",
        "rev_growth_acceleration",
    ], out_prefix="q3")
    df["q3_improving_score"] = q3

    # Composite = weighted average of attribute scores, ignoring NaN attrs.
    parts = {
        "q1_quality_score":   ATTRIBUTE_WEIGHTS["q1"],
        "q2_valuation_score": ATTRIBUTE_WEIGHTS["q2"],
        "q3_improving_score": ATTRIBUTE_WEIGHTS["q3"],
    }
    weighted_sum = pd.Series(0.0, index=df.index)
    weight_total = pd.Series(0.0, index=df.index)
    for col, w in parts.items():
        vals = df[col].astype(float)
        mask = vals.notna()
        weighted_sum = weighted_sum.add(vals.where(mask, 0) * w, fill_value=0)
        weight_total = weight_total.add(mask.astype(float) * w, fill_value=0)
    df["composite_score"] = weighted_sum / weight_total.replace(0, np.nan)
    df["composite_rank"] = df["composite_score"].rank(ascending=False, method="min")

    return CompositeResult(df=df)

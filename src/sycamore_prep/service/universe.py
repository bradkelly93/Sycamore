"""Universe status (read-only) + the Sycamore fund holdings overlay.

Building the universe is a Phase C background job; here we report whether
``universe.parquet`` exists and its composition, and we always surface the
committed Victory Sycamore fund holdings (Established Value + Small Company
Opportunity) so the Universe page is useful even before a build.
"""

from __future__ import annotations

from collections import Counter

from ..config import cache_dir
from ..universe.builder import load_universe
from ..universe.sycamore import HOLDINGS_SOURCE_TAG, load_sycamore_holdings
from . import artifacts
from .serde import clean_scalar
from .viewmodels import SycamoreHoldingRow, UniverseView, fig

_INSTRUCTIONS = (
    "The investable table (universe.parquet) isn't built yet. It will build from "
    "the committed Sycamore holdings below (plus the iShares IWS/IWN CSVs if you "
    "drop them in data/raw/) on the next screen/pipeline run, or via build-universe."
)


def _split_count(series) -> dict[str, int]:
    c: Counter = Counter()
    for s in series.dropna():
        for part in str(s).split(","):
            part = part.strip()
            if part:
                c[part] += 1
    return dict(c)


def _value_counts(series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.dropna().astype(str).value_counts().items()}


def _holding_rows(sh) -> list[SycamoreHoldingRow]:
    rows = []
    for r in sh.itertuples(index=False):
        w = r.weight_pct
        rows.append(SycamoreHoldingRow(
            fund=clean_scalar(r.fund),
            ticker=str(r.ticker),
            name=clean_scalar(r.name),
            gics_sector=clean_scalar(r.gics_sector),
            # CSV weight is a percent number (e.g. 2.6152) -> store as a fraction
            # so the "pct" formatter renders it correctly.
            weight_pct=fig((w / 100.0) if (w is not None and w == w) else None,
                           HOLDINGS_SOURCE_TAG, fmt="pct"),
            position_value=fig(r.position_value, HOLDINGS_SOURCE_TAG, fmt="ccy"),
        ))
    return rows


def status() -> UniverseView:
    sh = load_sycamore_holdings()
    syc_funds = _value_counts(sh["fund"]) if not sh.empty else {}
    syc_sectors = _value_counts(sh["gics_sector"]) if not sh.empty else {}
    syc_rows = _holding_rows(sh) if not sh.empty else []
    syc_note = None if syc_rows else "No Sycamore holdings file at data/sycamore_holdings.csv."

    df = load_universe()
    if df is None or len(df) == 0:
        return UniverseView(
            built=False, count=0, instructions=_INSTRUCTIONS,
            sycamore_funds=syc_funds, sycamore_sectors=syc_sectors,
            sycamore_holdings=syc_rows, sycamore_note=syc_note)

    csv_path = cache_dir() / "universe.csv"
    ref = artifacts.register(csv_path, "csv") if csv_path.exists() else None
    return UniverseView(
        built=True,
        count=int(len(df)),
        source_breakdown=_split_count(df["source"]) if "source" in df.columns else {},
        sector_breakdown=_value_counts(df["gics_sector"]) if "gics_sector" in df.columns else {},
        sycamore_owned=int(df["owned_by_sycamore"].sum()) if "owned_by_sycamore" in df.columns else 0,
        csv_artifact=ref,
        sycamore_funds=syc_funds,
        sycamore_sectors=syc_sectors,
        sycamore_holdings=syc_rows,
        sycamore_note=syc_note,
    )

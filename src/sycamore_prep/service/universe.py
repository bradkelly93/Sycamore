"""Universe status (read-only). Building the universe is a Phase C background job;
here we only report whether ``universe.parquet`` exists and its composition.
"""

from __future__ import annotations

from collections import Counter

from ..config import cache_dir
from ..universe.builder import load_universe
from . import artifacts
from .viewmodels import UniverseView

_INSTRUCTIONS = (
    "Drop the iShares holdings CSVs into data/raw/ (iws_holdings.csv, "
    "iwn_holdings.csv, optional sycamore_holdings.csv) — see data/raw/HOLDINGS.md "
    "— then build the universe. The build runs as a background job (Phase C)."
)


def status() -> UniverseView:
    df = load_universe()
    if df is None or len(df) == 0:
        return UniverseView(built=False, count=0, instructions=_INSTRUCTIONS)

    breakdown: dict[str, int] = {}
    if "source" in df.columns:
        c: Counter = Counter()
        for s in df["source"].dropna():
            for part in str(s).split(","):
                part = part.strip()
                if part:
                    c[part] += 1
        breakdown = dict(c)

    sycamore = int(df["owned_by_sycamore"].sum()) if "owned_by_sycamore" in df.columns else 0
    csv_path = cache_dir() / "universe.csv"
    ref = artifacts.register(csv_path, "csv") if csv_path.exists() else None

    return UniverseView(
        built=True,
        count=int(len(df)),
        source_breakdown=breakdown,
        sycamore_owned=sycamore,
        csv_artifact=ref,
    )

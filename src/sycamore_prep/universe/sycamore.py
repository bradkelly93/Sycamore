"""Victory Sycamore fund holdings (Established Value + Small Company Opportunity).

The funds' disclosed holdings, committed as ``data/sycamore_holdings.csv`` — the
default Sycamore overlay so the universe carries Sycamore's real names out of the
box (no manual data/raw drop, no network). A live EDGAR N-PORT pull could replace
this file later; the tidy schema here is the contract either way.

These are disclosed holdings provided as a file, so they are source-tagged as such
(NON-primary vs. a live EDGAR pull) — honest provenance per CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import project_root

# Universe provenance tag (matches the existing overlay convention).
SYCAMORE_SOURCE = "sycamore-overlay"
# Per-figure source tag shown in the UI.
HOLDINGS_SOURCE_TAG = "victory sycamore funds (disclosed holdings)"

_DETAIL_COLS = ["fund", "ticker", "name", "gics_sector", "shares",
                "position_value", "weight_pct", "description", "source"]
_UNIVERSE_COLS = ["ticker", "name", "gics_sector", "market_cap", "weight_pct", "source"]


def sycamore_holdings_path() -> Path:
    return project_root() / "data" / "sycamore_holdings.csv"


def _norm_ticker(t) -> str:
    return str(t).strip().upper().replace(".", "-")


def load_sycamore_holdings(path: Path | str | None = None) -> pd.DataFrame:
    """Tidy per-holding detail across both funds. Cash / non-equity rows (blank
    ticker) are dropped. Columns: fund, ticker, name, gics_sector, shares,
    position_value, weight_pct, description, source."""
    p = Path(path) if path else sycamore_holdings_path()
    if not p.exists():
        return pd.DataFrame(columns=_DETAIL_COLS)
    df = pd.read_csv(p)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.rename(columns={"company": "name", "sector": "gics_sector",
                            "market value": "position_value", "weight %": "weight_pct"})
    for c in _DETAIL_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df["ticker"] = df["ticker"].map(_norm_ticker)
    df = df[df["ticker"].notna() & ~df["ticker"].isin(["", "NAN", "NONE"])]
    for c in ("shares", "position_value", "weight_pct"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["source"] = SYCAMORE_SOURCE
    return df[_DETAIL_COLS].reset_index(drop=True)


def sycamore_overlay_frame(path: Path | str | None = None) -> pd.DataFrame:
    """Reduce the holdings to the universe-builder schema (dedup happens in
    ``build_universe``). ``market_cap`` is left blank — the CSV's position value is
    NOT the company's market cap, and the screener fills the real cap later."""
    h = load_sycamore_holdings(path)
    return pd.DataFrame({
        "ticker": h["ticker"],
        "name": h["name"],
        "gics_sector": h["gics_sector"],
        "market_cap": pd.NA,
        "weight_pct": h["weight_pct"],
        "source": SYCAMORE_SOURCE,
    })[_UNIVERSE_COLS]

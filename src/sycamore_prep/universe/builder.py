"""Universe builder.

Inputs (drop in `data/raw/`):
  - iws_holdings.csv  — iShares Russell Mid-Cap Value (IWS)
  - iwn_holdings.csv  — iShares Russell 2000 Value   (IWN)
  - sycamore_holdings.csv — Sycamore fund holdings (optional overlay)

iShares holdings CSVs ship with a banner of metadata rows above the column
header. We sniff for the header row dynamically so the user can drop the file
in unchanged.

Output:
  - data/cache/universe.parquet
  - data/cache/universe.csv (human-readable)

Every row carries `source` tags ("ishares-iws", "ishares-iwn",
"sycamore-overlay") and the `in_iws`, `in_iwn`, `owned_by_sycamore` flags.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..config import cache_dir, load_config, project_root, raw_dir


# Candidate column names across iShares / generic feeds. Lower-cased match.
_TICKER_CANDIDATES = ["ticker", "issuer ticker", "symbol", "holding ticker"]
_NAME_CANDIDATES   = ["name", "issuer name", "security name", "holding name"]
_SECTOR_CANDIDATES = ["sector", "gics sector", "industry sector"]
_MCAP_CANDIDATES   = ["market value", "market cap", "marketcap", "market capitalization"]
_WEIGHT_CANDIDATES = ["weight (%)", "weight(%)", "% of net assets", "weight", "portfolio weight"]


def _sniff_header_row(path: Path, max_scan: int = 40) -> int:
    """iShares CSVs have ~9 lines of metadata before the table header.
    Return the 0-based index of the first row that looks like a header.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for i, row in enumerate(reader):
            if i >= max_scan:
                break
            cells_lc = [c.strip().lower() for c in row]
            has_ticker = any(c in _TICKER_CANDIDATES for c in cells_lc)
            has_name = any(c in _NAME_CANDIDATES for c in cells_lc)
            if has_ticker and has_name:
                return i
    return 0  # fall back; pd.read_csv will surface the real error


def _first_match(cols: Iterable[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower().strip(): c for c in cols}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _load_holdings_csv(path: Path, source_tag: str) -> pd.DataFrame:
    header_row = _sniff_header_row(path)
    # `skip_blank_lines=False` keeps pandas' row indexing aligned with what
    # our csv.reader sniffer saw — otherwise pd silently drops blank rows
    # in the banner and the header= offset is wrong by N-blanks.
    df = pd.read_csv(
        path,
        header=header_row,
        dtype=str,
        keep_default_na=False,
        skip_blank_lines=False,
    )
    df.columns = [c.strip() for c in df.columns]

    t_col = _first_match(df.columns, _TICKER_CANDIDATES)
    n_col = _first_match(df.columns, _NAME_CANDIDATES)
    s_col = _first_match(df.columns, _SECTOR_CANDIDATES)
    m_col = _first_match(df.columns, _MCAP_CANDIDATES)
    w_col = _first_match(df.columns, _WEIGHT_CANDIDATES)

    if not t_col or not n_col:
        raise ValueError(
            f"Could not find ticker/name columns in {path.name}. "
            f"Found columns: {list(df.columns)}"
        )

    out = pd.DataFrame({
        "ticker": df[t_col].str.upper().str.strip(),
        "name": df[n_col].str.strip(),
        "gics_sector": df[s_col].str.strip() if s_col else pd.NA,
        "market_cap": pd.to_numeric(df[m_col].str.replace(",", ""), errors="coerce") if m_col else pd.NA,
        "weight_pct": pd.to_numeric(df[w_col].str.replace("%", "").str.replace(",", ""), errors="coerce") if w_col else pd.NA,
    })
    # Filter out cash / non-equity rows that iShares includes.
    out = out[out["ticker"].str.len().between(1, 10)]
    out = out[~out["ticker"].isin({"-", "USD", "CASH"})]
    out["source"] = source_tag
    return out.reset_index(drop=True)


def build_universe(
    iws_path: Path | None = None,
    iwn_path: Path | None = None,
    sycamore_path: Path | None = None,
) -> pd.DataFrame:
    """Build the master universe table. Returns the DataFrame and writes
    cache outputs as a side effect.
    """
    cfg = load_config()
    raw = raw_dir()
    iws_path = iws_path or (raw / cfg.raw.iws_holdings)
    iwn_path = iwn_path or (raw / cfg.raw.iwn_holdings)
    sycamore_path = sycamore_path or (raw / cfg.raw.sycamore_holdings)

    frames: list[pd.DataFrame] = []
    if iws_path.exists():
        frames.append(_load_holdings_csv(iws_path, "ishares-iws"))
    if iwn_path.exists():
        frames.append(_load_holdings_csv(iwn_path, "ishares-iwn"))
    if sycamore_path.exists():
        frames.append(_load_holdings_csv(sycamore_path, "sycamore-overlay"))

    if not frames:
        raise FileNotFoundError(
            "No holdings CSVs found in data/raw/. Drop at least one of "
            f"{cfg.raw.iws_holdings}, {cfg.raw.iwn_holdings}, "
            f"{cfg.raw.sycamore_holdings} and rerun."
        )

    combined = pd.concat(frames, ignore_index=True)
    combined["in_iws"] = combined["source"].eq("ishares-iws")
    combined["in_iwn"] = combined["source"].eq("ishares-iwn")
    combined["owned_by_sycamore"] = combined["source"].eq("sycamore-overlay")

    # Aggregate to one row per ticker.
    agg = (
        combined.groupby("ticker", as_index=False)
        .agg({
            "name": "first",
            "gics_sector": "first",
            "market_cap": "max",
            "in_iws": "max",
            "in_iwn": "max",
            "owned_by_sycamore": "max",
        })
    )
    # Source column lists every provider that contributed.
    src = (
        combined.groupby("ticker")["source"]
        .apply(lambda s: ",".join(sorted(set(s))))
        .reset_index()
    )
    agg = agg.merge(src, on="ticker", how="left")
    agg = agg.sort_values(["owned_by_sycamore", "in_iwn", "in_iws", "ticker"],
                         ascending=[False, False, False, True]).reset_index(drop=True)

    # Write outputs.
    out_dir = cache_dir()
    parquet_path = out_dir / "universe.parquet"
    csv_path = out_dir / "universe.csv"
    agg.to_parquet(parquet_path, index=False)
    agg.to_csv(csv_path, index=False)
    return agg


def load_universe() -> pd.DataFrame | None:
    p = cache_dir() / "universe.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)

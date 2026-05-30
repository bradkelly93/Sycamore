"""Phase 6 — the pipeline orchestrator: one funnel, minimal intervention.

Chains the existing engines (no recompute) into a downside-first funnel:

    universe -> screen -> shortlist (3-attribute) -> per name: comps + model
             -> per-name dossier + a ranked index + a run manifest

Cache is the coordination layer: every engine caches to data/cache/ keyed by
ticker+concept+period, so a pipeline re-run is idempotent, resumable, and
offline-replayable; only missing/stale data is re-pulled (``--refresh`` forces).

Selection modes compose: top-N by composite rank (default), the Sycamore-owned
overlay, a GICS sector slice, and/or an explicit ticker list. Peers resolve from
config.peers, else are auto-derived from the universe by sector + nearest market
cap (so any name gets a comp set). Everything stays CLI + xlsx/csv, source-
tagged and three-attribute-decomposed (CLAUDE.md) — an orchestrator, not a UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .comps import run_comps
from .comps.report import _df_to_sheet, _safe
from .config import cache_dir, load_config
from .models import build_models
from .screener import run_screener
from .spinoffs.tracker import run_scan
from .universe.builder import build_universe, load_universe

DEFAULT_PEER_K = 5


@dataclass
class Dossier:
    ticker: str
    name: str | None = None
    gics_sector: str | None = None
    market_cap: float | None = None
    q1_quality_score: float | None = None
    q2_valuation_score: float | None = None
    q3_improving_score: float | None = None
    composite_score: float | None = None
    composite_rank: float | None = None
    margin_of_safety_base: float | None = None
    implied_growth_base: float | None = None
    trough_pe: float | None = None
    ns_flags: str = ""
    is_bank: bool = False
    recent_spinoff: bool = False
    peers_used: str = ""
    dossier_dir: str = ""
    error: str | None = None


@dataclass
class PipelineResult:
    shortlist: list[str]
    dossiers: list[Dossier]
    out_dir: Path
    index_xlsx: Path | None = None
    index_md: Path | None = None
    manifest_path: Path | None = None
    screened: pd.DataFrame | None = field(default=None, repr=False)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #

def candidate_tickers(uni: pd.DataFrame, sector: str | None, sycamore_only: bool) -> list[str]:
    """Universe tickers after applying the (composable) sector + overlay filters."""
    df = uni
    if sycamore_only and "owned_by_sycamore" in df.columns:
        df = df[df["owned_by_sycamore"].astype(bool)]
    if sector and "gics_sector" in df.columns:
        df = df[df["gics_sector"].astype(str).str.contains(sector, case=False, na=False)]
    return df["ticker"].astype(str).str.upper().tolist()


def shortlist_from_screen(screened: pd.DataFrame, top: int, keep_flagged: bool) -> list[str]:
    """Top-N by composite_rank. Hard negative-space flags are excluded unless
    keep_flagged (explicit-ticker mode keeps everything the user asked for)."""
    df = screened
    if not keep_flagged and "negative_space" in df.columns:
        df = df[~df["negative_space"].astype(bool)]
    if "composite_rank" in df.columns:
        df = df[df["composite_rank"].notna()].sort_values("composite_rank")
    return [str(t) for t in df.index[:top]]


def derive_peers(uni: pd.DataFrame | None, ticker: str, k: int = DEFAULT_PEER_K) -> list[str]:
    """Peers = same GICS sector, nearest market cap (log distance). Empty if the
    ticker/sector isn't in the universe."""
    if uni is None or uni.empty or "gics_sector" not in uni.columns:
        return []
    u = uni.copy()
    u["ticker"] = u["ticker"].astype(str).str.upper()
    row = u[u["ticker"] == ticker.upper()]
    if row.empty:
        return []
    sector = row.iloc[0]["gics_sector"]
    mcap = row.iloc[0].get("market_cap", np.nan)
    pool = u[(u["gics_sector"] == sector) & (u["ticker"] != ticker.upper())].copy()
    pool = pool[pool["market_cap"].notna()] if "market_cap" in pool.columns else pool
    if pool.empty:
        return []
    if pd.isna(mcap) or mcap <= 0:
        return pool["ticker"].head(k).tolist()
    pool = pool[pool["market_cap"] > 0]
    pool["_dist"] = (np.log(pool["market_cap"]) - np.log(float(mcap))).abs()
    return pool.sort_values("_dist")["ticker"].head(k).tolist()


def _index_frame(dossiers: list[Dossier]) -> pd.DataFrame:
    """Ranked review table — downside metrics (MoS, flags) sit next to the scores."""
    cols = ["ticker", "name", "gics_sector", "market_cap",
            "composite_rank", "q1_quality_score", "q2_valuation_score",
            "q3_improving_score", "margin_of_safety_base", "implied_growth_base",
            "trough_pe", "ns_flags", "is_bank", "recent_spinoff",
            "peers_used", "dossier_dir", "error"]
    df = pd.DataFrame([{c: getattr(d, c) for c in cols} for d in dossiers])
    if not df.empty:
        df = df.sort_values("composite_rank", na_position="last").set_index("ticker")
    return df


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _ensure_universe() -> pd.DataFrame:
    uni = load_universe()
    if uni is None or uni.empty:
        uni = build_universe()   # builds from data/raw holdings CSVs
    return uni


def _select(tickers, sector, sycamore_only, top, skip_market_cap):
    """Return (screened_df, shortlist, universe). Explicit tickers skip the
    universe filter but are still scored so the index carries sub-scores."""
    explicit = bool(tickers)
    uni = None
    if explicit:
        cand = [t.upper() for t in tickers]
    else:
        uni = _ensure_universe()
        cand = candidate_tickers(uni, sector, sycamore_only)
        if not cand:
            raise ValueError("No candidates after filters (sector / sycamore-only).")
    screened = run_screener(tickers=cand, skip_market_cap=skip_market_cap)
    shortlist = shortlist_from_screen(screened, top, keep_flagged=explicit)
    if uni is None:
        uni = load_universe()   # for peer derivation, if available
    return screened, shortlist, uni


def _recent_spinoff_tickers(refresh: bool) -> set[str]:
    try:
        res = run_scan(refresh=refresh)
    except Exception:  # noqa: BLE001 — scan is network-heavy + optional
        return set()
    df = res.df
    out: set[str] = set()
    for col in ("spinco_ticker", "parent_ticker"):
        if col in df.columns:
            out |= {str(t).upper() for t in df[col].dropna().tolist()}
    return out


def _write_index(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    from openpyxl import Workbook
    xlsx = out_dir / "index.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "shortlist"
    _df_to_sheet(ws, df, "ticker")
    wb.save(xlsx)

    md = out_dir / "index.md"
    L = ["# Pipeline shortlist (downside-first)", "",
         "_Fundamentals: SEC EDGAR (primary). Prices/market cap: yfinance (non-primary)._", "",
         "| Rank | Ticker | Name | Q1 Qual | Q2 Val | Q3 Impr | MoS (base) | Implied g | Flags | Dossier |",
         "|--:|---|---|--:|--:|--:|--:|--:|---|---|"]
    for tk, r in df.iterrows():
        def _n(x, p=0):
            return "n/a" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:.{p}f}"
        def _pct(x):
            return "n/a" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:.1f}%"
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            _n(r.get("composite_rank")), tk, r.get("name") or "",
            _n(r.get("q1_quality_score")), _n(r.get("q2_valuation_score")),
            _n(r.get("q3_improving_score")), _pct(r.get("margin_of_safety_base")),
            _pct(r.get("implied_growth_base")), r.get("ns_flags") or "",
            r.get("dossier_dir") or ""))
    md.write_text("\n".join(L) + "\n", encoding="utf-8")
    return xlsx, md


def _write_manifest(out_dir: Path, params: dict, dossiers: list[Dossier]) -> Path:
    def _mtime(p: Path):
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat() if p.exists() else None
    cd = cache_dir()
    per_ticker = {}
    for d in dossiers:
        per_ticker[d.ticker] = {
            "is_bank": d.is_bank, "peers_used": d.peers_used, "error": d.error,
            "financials_cached": _mtime(cd / f"financials_{d.ticker}.parquet"),
            "prices_cached": _mtime(cd / f"prices_{d.ticker}.parquet"),
        }
    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "sources": "SEC EDGAR XBRL (primary); yfinance prices/market cap (non-primary)",
        "shortlist": [d.ticker for d in dossiers],
        "per_ticker": per_ticker,
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def run_pipeline(
    tickers: list[str] | None = None,
    *,
    sector: str | None = None,
    sycamore_only: bool = False,
    top: int = 10,
    wacc: float | None = None,
    terminal_growth: float | None = None,
    forecast_years: int | None = None,
    share_basis: str = "wad",
    auto_peers: bool = True,
    link_spinoffs: bool = False,
    skip_market_cap: bool = False,
    output_dir: Path | str | None = None,
    refresh: bool = False,
) -> PipelineResult:
    """Run the full funnel and write a self-contained run folder under
    data/cache/pipeline_<timestamp>/ (gitignored — real data)."""
    cfg = load_config()
    screened, shortlist, uni = _select(tickers, sector, sycamore_only, top, skip_market_cap)

    spin_tickers = _recent_spinoff_tickers(refresh) if link_spinoffs else set()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else (cache_dir() / f"pipeline_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    dossiers: list[Dossier] = []
    for tk in shortlist:
        d = Dossier(ticker=tk, recent_spinoff=tk in spin_tickers)
        if tk in screened.index:
            srow = screened.loc[tk]
            for c in ("name", "gics_sector", "market_cap", "composite_score",
                      "composite_rank", "q1_quality_score", "q2_valuation_score",
                      "q3_improving_score", "ns_flags", "is_bank"):
                if c in screened.columns:
                    setattr(d, c, _safe(srow[c]))
        ddir = out_dir / tk
        ddir.mkdir(parents=True, exist_ok=True)
        d.dossier_dir = tk
        try:
            peers = list(cfg.peers.get(tk, []))
            if not peers and auto_peers:
                peers = derive_peers(uni, tk)
            d.peers_used = ", ".join(peers)
            comps = run_comps(
                tk, peers or None, wacc=wacc, terminal_growth=terminal_growth,
                forecast_years=forecast_years, share_basis=share_basis,
                output_path=ddir / f"comps_{tk}.xlsx", refresh=refresh,
            )
            res = build_models(
                tk, peers=peers or None, wacc=wacc, terminal_growth=terminal_growth,
                forecast_years=forecast_years, share_basis=share_basis,
                output_path=ddir / f"{tk}_model.xlsx", comps_result=comps,
            )
            d.is_bank = res.is_bank
            sub = comps.subject
            d.implied_growth_base = _safe(sub.base_implied_growth())
            d.margin_of_safety_base = _safe(sub.base_margin_of_safety())
            d.trough_pe = _safe(sub.normalized.trough_pe) if sub.normalized else None
            if sub.error:
                d.error = sub.error
        except Exception as exc:  # noqa: BLE001 — one bad name never kills the run
            d.error = f"{type(exc).__name__}: {exc}"
        dossiers.append(d)

    idx = _index_frame(dossiers)
    index_xlsx, index_md = _write_index(idx, out_dir)
    params = {
        "tickers": tickers, "sector": sector, "sycamore_only": sycamore_only,
        "top": top, "auto_peers": auto_peers, "link_spinoffs": link_spinoffs,
        "wacc": wacc, "terminal_growth": terminal_growth,
        "forecast_years": forecast_years, "share_basis": share_basis,
    }
    manifest_path = _write_manifest(out_dir, params, dossiers)

    return PipelineResult(
        shortlist=shortlist, dossiers=dossiers, out_dir=out_dir,
        index_xlsx=index_xlsx, index_md=index_md, manifest_path=manifest_path,
        screened=screened,
    )

"""Service wrapper for the pipeline funnel — READ-ONLY in Phase A.

Renders an existing ``data/cache/pipeline_<ts>/`` run folder (index.xlsx +
manifest.json + per-ticker dossiers) into a downside-first :class:`PipelineView`.
Running a new pipeline is a Phase C background job (``service.jobs``); this module
only reads what's already on disk. Runs are addressed by an artifact ``run_token``
(never a raw path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..config import cache_dir
from . import artifacts
from .serde import clean_scalar, split_flags
from .viewmodels import (
    EDGAR,
    YFINANCE,
    DossierView,
    PipelineRunRef,
    PipelineView,
    fig,
)

MIXED = "edgar + yfinance price (non-primary)"


def _read_manifest(run_dir: Path) -> dict:
    p = run_dir / "manifest.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _to_bool(v) -> bool:
    v = clean_scalar(v)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y", "t")


def _to_int(v) -> int | None:
    v = clean_scalar(v)
    return int(v) if v is not None else None


def _dossier_artifacts(run_dir: Path, ticker: str, dossier_dir: str | None) -> dict:
    ddir = run_dir / (dossier_dir or ticker)
    out = {}
    for key, fname, kind in (
        ("comps_xlsx", f"comps_{ticker}.xlsx", "xlsx"),
        ("model_xlsx", f"{ticker}_model.xlsx", "xlsx"),
        ("comps_md", f"comps_{ticker}.md", "md"),
    ):
        fp = ddir / fname
        if fp.exists():
            out[key] = artifacts.register(fp, kind)
    return out


def _dossier_from_row(run_dir: Path, row: pd.Series, per_ticker: dict) -> DossierView:
    tk = str(clean_scalar(row.get("ticker")))
    pt = per_ticker.get(tk, {})
    mos = clean_scalar(row.get("margin_of_safety_base"))
    return DossierView(
        ticker=tk,
        name=clean_scalar(row.get("name")),
        gics_sector=clean_scalar(row.get("gics_sector")),
        market_cap=fig(row.get("market_cap"), YFINANCE, fmt="ccy"),
        q1_quality=fig(row.get("q1_quality_score"), EDGAR),
        q2_valuation=fig(row.get("q2_valuation_score"), MIXED),
        q3_improving=fig(row.get("q3_improving_score"), EDGAR),
        composite_rank=_to_int(row.get("composite_rank")),
        margin_of_safety_base=fig(mos, MIXED, fmt="pct",
                                  flag="risk" if (mos is not None and mos < 0) else None),
        implied_growth_base=fig(row.get("implied_growth_base"), MIXED, fmt="pct"),
        trough_pe=fig(row.get("trough_pe"), MIXED, fmt="mult", flag="warn"),
        ns_flags=split_flags(row.get("ns_flags")),
        is_bank=_to_bool(row.get("is_bank")) if "is_bank" in row else _to_bool(pt.get("is_bank")),
        recent_spinoff=_to_bool(row.get("recent_spinoff")),
        peers_used=split_flags(row.get("peers_used")) or split_flags(pt.get("peers_used")),
        peers_source=clean_scalar(row.get("peers_source")) or "",
        dossier_artifacts=_dossier_artifacts(run_dir, tk, clean_scalar(row.get("dossier_dir"))),
        error=clean_scalar(row.get("error")) or clean_scalar(pt.get("error")),
    )


def _read_dossiers(run_dir: Path, manifest: dict) -> list[DossierView]:
    xlsx = run_dir / "index.xlsx"
    per_ticker = manifest.get("per_ticker", {})
    if xlsx.exists():
        df = pd.read_excel(xlsx)
        if "ticker" not in df.columns and len(df.columns):
            df = df.rename(columns={df.columns[0]: "ticker"})
        return [_dossier_from_row(run_dir, row, per_ticker) for _, row in df.iterrows()]
    # Partial run with no index — fall back to the manifest shortlist.
    out = []
    for tk in manifest.get("shortlist", []):
        pt = per_ticker.get(tk, {})
        out.append(DossierView(ticker=tk, is_bank=_to_bool(pt.get("is_bank")),
                               peers_used=split_flags(pt.get("peers_used")),
                               error=clean_scalar(pt.get("error"))))
    return out


def list_runs() -> list[PipelineRunRef]:
    runs: list[PipelineRunRef] = []
    for d in sorted(cache_dir().glob("pipeline_*"), reverse=True):
        if not d.is_dir():
            continue
        m = _read_manifest(d)
        ref = artifacts.register(d, "folder")
        runs.append(PipelineRunRef(run_token=ref.token, name=d.name,
                                   generated=m.get("generated"),
                                   shortlist=m.get("shortlist", [])))
    return runs


def load(run_token: str) -> PipelineView:
    run_dir = artifacts.resolve(run_token)
    m = _read_manifest(run_dir)
    index_artifacts = {}
    if (run_dir / "index.xlsx").exists():
        index_artifacts["xlsx"] = artifacts.register(run_dir / "index.xlsx", "xlsx")
    if (run_dir / "index.md").exists():
        index_artifacts["md"] = artifacts.register(run_dir / "index.md", "md")
    manifest_artifact = (artifacts.register(run_dir / "manifest.json", "json")
                         if (run_dir / "manifest.json").exists() else None)
    return PipelineView(
        run_token=run_token,
        name=run_dir.name,
        generated=m.get("generated"),
        params=m.get("params", {}),
        sources=m.get("sources"),
        shortlist=m.get("shortlist", []),
        dossiers=_read_dossiers(run_dir, m),
        index_artifacts=index_artifacts,
        manifest_artifact=manifest_artifact,
        manifest=m,
    )


def latest() -> PipelineView | None:
    runs = list_runs()
    return load(runs[0].run_token) if runs else None

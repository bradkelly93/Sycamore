"""PipelineView: reads an existing pipeline_<ts>/ folder, downside-first dossiers,
separate sub-scores, artifacts as refs."""

from __future__ import annotations

import json

import pandas as pd

from sycamore_prep.service import pipeline as svc
from sycamore_prep.service.viewmodels import ArtifactRef


def _make_run(cache):
    run = cache / "pipeline_20260101_120000"
    run.mkdir()
    idx = pd.DataFrame({
        "ticker": ["CW", "WES"],
        "name": ["Curtiss-Wright", "Western Midstream"],
        "gics_sector": ["Industrials", "Energy"],
        "market_cap": [5e9, None],
        "composite_rank": [1, 2],
        "q1_quality_score": [80.0, 60.0],
        "q2_valuation_score": [70.0, 65.0],
        "q3_improving_score": [55.0, None],
        "margin_of_safety_base": [0.2, -0.1],
        "implied_growth_base": [0.03, 0.05],
        "trough_pe": [22.0, None],
        "ns_flags": ["", "high_leverage"],
        "is_bank": [False, False],
        "recent_spinoff": [False, False],
        "peers_used": ["HEI, TDG", "ENLC, DTM"],
        "peers_source": ["config", "sector"],
        "dossier_dir": ["CW", "WES"],
        "error": [None, None],
    })
    idx.to_excel(run / "index.xlsx", index=False)
    (run / "index.md").write_text("# index")
    for tk in ("CW", "WES"):
        d = run / tk
        d.mkdir()
        (d / f"comps_{tk}.xlsx").write_text("x")
        (d / f"{tk}_model.xlsx").write_text("x")
    manifest = {
        "generated": "2026-01-01T12:00:00",
        "params": {"top": 10},
        "sources": "SEC EDGAR XBRL (primary); yfinance (non-primary)",
        "shortlist": ["CW", "WES"],
        "per_ticker": {"CW": {"is_bank": False, "peers_used": "HEI, TDG", "error": None}},
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    return run


def test_list_and_load_run(wl_root):
    _make_run(wl_root.cache)
    runs = svc.list_runs()
    assert len(runs) == 1 and runs[0].name == "pipeline_20260101_120000"
    view = svc.load(runs[0].run_token)
    assert view.shortlist == ["CW", "WES"]
    assert [d.ticker for d in view.dossiers] == ["CW", "WES"]
    assert view.params == {"top": 10}


def test_dossier_downside_and_separate_scores(wl_root):
    _make_run(wl_root.cache)
    view = svc.latest()
    cw = next(d for d in view.dossiers if d.ticker == "CW")
    wes = next(d for d in view.dossiers if d.ticker == "WES")
    assert (cw.q1_quality.value, cw.q2_valuation.value, cw.q3_improving.value) == (80.0, 70.0, 55.0)
    assert wes.margin_of_safety_base.flag == "risk"     # negative MoS
    assert cw.margin_of_safety_base.flag is None
    assert wes.market_cap.value is None and wes.q3_improving.value is None   # NaN -> None
    assert cw.peers_used == ["HEI", "TDG"]


def test_dossier_artifacts_are_refs(wl_root):
    _make_run(wl_root.cache)
    view = svc.latest()
    cw = next(d for d in view.dossiers if d.ticker == "CW")
    assert isinstance(cw.dossier_artifacts["comps_xlsx"], ArtifactRef)
    assert isinstance(cw.dossier_artifacts["model_xlsx"], ArtifactRef)
    assert isinstance(view.index_artifacts["xlsx"], ArtifactRef)
    assert view.manifest_artifact is not None


def test_pipeline_view_json_safe(wl_root):
    _make_run(wl_root.cache)
    json.loads(svc.latest().model_dump_json())

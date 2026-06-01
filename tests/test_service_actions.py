"""Phase B/C action wrappers: build-model, vol, spin-track, and the two jobs."""

from __future__ import annotations

import json
import time

import pandas as pd

from sycamore_prep.comps.comps import CompsResult, TickerAnalysis
from sycamore_prep.comps.reverse_dcf import DcfCase
from sycamore_prep.service import actions, jobs
from sycamore_prep.service.viewmodels import ActionResult, ArtifactRef


def _subject(mos=0.2):
    return TickerAnalysis(
        ticker="CW", name="Curtiss-Wright", is_bank=False, market_cap=5e9, price=50.0,
        dcf_cases=[DcfCase(label="base", wacc=0.09, terminal_growth=0.025, fcf0=100.0,
                           implied_growth=0.04, implied_converged=True, assumed_growth=0.03,
                           fair_value_per_share=60.0, current_price=50.0, margin_of_safety=mos)],
        sources="edgar (primary)", error=None)


class _ModelRes:
    def __init__(self, path):
        self.ticker = "CW"
        self.xlsx_path = path
        self.is_bank = False
        self.has_sotp = False
        self.comps_result = CompsResult(subject=_subject(), peers=[], peer_table=pd.DataFrame(),
                                        wacc=0.09, terminal_growth=0.025, forecast_years=10,
                                        share_basis="wad")


def test_build_model_action_ok(wl_root, monkeypatch):
    xlsx = wl_root.models / "CW_model.xlsx"
    xlsx.write_text("x")
    monkeypatch.setattr(actions, "build_models", lambda *a, **k: _ModelRes(xlsx))
    r = actions.build_model("cw")
    assert isinstance(r, ActionResult) and r.ok is True
    assert r.artifacts and isinstance(r.artifacts[0], ArtifactRef)
    assert r.link == "/workup/CW"
    assert "Margin of safety (base)" in r.detail
    json.loads(r.model_dump_json())


def test_build_model_action_surfaces_error(wl_root, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no data")
    monkeypatch.setattr(actions, "build_models", boom)
    r = actions.build_model("ZZZ")
    assert r.ok is False and "no data" in r.message


def test_vol_action_degrades_without_creds(wl_root, monkeypatch):
    monkeypatch.setattr(actions.TastytradeProvider, "available", staticmethod(lambda: False))
    r = actions.run_vol("CW")
    assert r.ok is False and "TASTYTRADE" in r.message     # clear note, no traceback


def test_rebuild_universe_job(wl_root, monkeypatch):
    df = pd.DataFrame({"ticker": ["CW", "WES"], "owned_by_sycamore": [True, False]})
    monkeypatch.setattr(actions, "build_universe", lambda: df)
    (wl_root.cache / "universe.csv").write_text("x")
    out = actions.rebuild_universe_job()
    assert out["summary"] == {"companies": 2, "sycamore_owned": 1}
    assert out["url"] == "/universe"
    assert isinstance(out["artifact"], ArtifactRef)


def test_run_pipeline_job(wl_root, monkeypatch):
    run_dir = wl_root.cache / "pipeline_20260101_000000"
    run_dir.mkdir()

    class _Dossier:
        error = None

    class _Res:
        shortlist = ["CW", "WES"]
        dossiers = [_Dossier(), _Dossier()]
        out_dir = run_dir
    monkeypatch.setattr(actions, "run_pipeline", lambda **k: _Res())
    out = actions.run_pipeline_job(tickers=["CW", "WES"])
    assert out["summary"]["shortlist"] == 2 and out["summary"]["errors"] == 0
    assert out["url"].startswith("/pipeline/")


def test_jobs_runner_surfaces_summary_and_url():
    jid = jobs.submit("t", lambda: {"summary": {"companies": 5}, "url": "/universe"})
    for _ in range(100):
        v = jobs.get(jid)
        if v.state in ("done", "error"):
            break
        time.sleep(0.02)
    assert v.state == "done"
    assert v.result_summary == {"companies": 5} and v.result_url == "/universe"

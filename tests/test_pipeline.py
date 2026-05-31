"""Phase 6 — pipeline orchestrator tests (OFFLINE).

Pure selection/peer helpers are tested directly; the end-to-end funnel is tested
with the engine entry points monkeypatched to synthetic stubs, so it exercises
selection → per-name dossier → ranked index + manifest assembly with no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sycamore_prep import pipeline as pl
from sycamore_prep.comps.comps import CompsResult
from sycamore_prep.models.builder import ModelBuildResult
from tests.test_models_builder import make_model_inputs

_M = 1_000_000


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": "AAA", "name": "Alpha", "gics_sector": "Industrials",
         "market_cap": 2000 * _M, "owned_by_sycamore": True},
        {"ticker": "BBB", "name": "Beta", "gics_sector": "Industrials",
         "market_cap": 2200 * _M, "owned_by_sycamore": False},
        {"ticker": "CCC", "name": "Gamma", "gics_sector": "Industrials",
         "market_cap": 8000 * _M, "owned_by_sycamore": False},
        {"ticker": "DDD", "name": "Delta", "gics_sector": "Financials",
         "market_cap": 1500 * _M, "owned_by_sycamore": True},
    ])


def _screened(tickers: list[str]) -> pd.DataFrame:
    rank = {t: i + 1 for i, t in enumerate(tickers)}
    rows = []
    for t in tickers:
        rows.append({
            "ticker": t, "name": f"{t} Inc", "gics_sector": "Industrials",
            "market_cap": 2000 * _M, "is_bank": False,
            "composite_rank": rank[t], "composite_score": 90 - rank[t],
            "q1_quality_score": 70.0, "q2_valuation_score": 60.0,
            "q3_improving_score": 50.0,
            "negative_space": (t == "ZZZ"), "ns_flags": "extreme_pe" if t == "ZZZ" else "",
        })
    return pd.DataFrame(rows).set_index("ticker")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def test_candidate_tickers_filters_compose():
    uni = _universe()
    assert set(pl.candidate_tickers(uni, None, False)) == {"AAA", "BBB", "CCC", "DDD"}
    assert set(pl.candidate_tickers(uni, "indus", False)) == {"AAA", "BBB", "CCC"}
    assert set(pl.candidate_tickers(uni, None, True)) == {"AAA", "DDD"}
    assert set(pl.candidate_tickers(uni, "indus", True)) == {"AAA"}   # both filters


def test_shortlist_excludes_flagged_unless_kept():
    df = _screened(["AAA", "BBB", "ZZZ", "CCC"])  # ZZZ flagged
    assert pl.shortlist_from_screen(df, top=10, keep_flagged=False) == ["AAA", "BBB", "CCC"]
    # keep_flagged keeps ZZZ and respects rank order
    assert pl.shortlist_from_screen(df, top=10, keep_flagged=True) == ["AAA", "BBB", "ZZZ", "CCC"]
    assert pl.shortlist_from_screen(df, top=2, keep_flagged=False) == ["AAA", "BBB"]


def test_derive_peers_nearest_market_cap_in_sector():
    uni = _universe()
    # AAA (2.0bn industrials): BBB (2.2bn) is nearer than CCC (8bn); DDD is financials.
    peers, source = pl.derive_peers(uni, "AAA", k=5)
    assert source == "sector"          # no sic_of → sector+mcap fallback
    assert peers[0] == "BBB"
    assert "DDD" not in peers
    assert set(peers) <= {"BBB", "CCC"}


def test_derive_peers_narrows_to_sic_group_when_available():
    """With a sic_of lookup, peers narrow to the subject's 2-digit SIC group —
    so a same-sector but different-business-model name is excluded even if its
    market cap is closer. (Mirrors the real CAR-got-shippers problem.)"""
    uni = _universe()  # AAA, BBB, CCC all Industrials
    # AAA is SIC 75xx (auto rental); BBB + CCC are 75xx too; add a 42xx trucking
    # name that's the NEAREST in market cap but a different business model.
    uni = pd.concat([uni, pd.DataFrame([
        {"ticker": "TRK", "name": "Trucking Co", "gics_sector": "Industrials",
         "market_cap": 2010 * _M, "owned_by_sycamore": False},
    ])], ignore_index=True)
    sic = {"AAA": "7510", "BBB": "7513", "CCC": "7512", "TRK": "4213"}
    peers, source = pl.derive_peers(uni, "AAA", k=5, sic_of=lambda t: sic.get(t))
    assert source == "sic"
    assert "TRK" not in peers           # nearest mcap, but wrong SIC group → excluded
    assert set(peers) <= {"BBB", "CCC"}


def test_derive_peers_falls_back_to_sector_when_sic_group_too_thin():
    uni = _universe()
    # Only AAA is 75xx; everyone else differs → <2 same-group peers → fall back.
    sic = {"AAA": "7510", "BBB": "3559", "CCC": "2834"}
    peers, source = pl.derive_peers(uni, "AAA", k=5, sic_of=lambda t: sic.get(t))
    assert source == "sector"
    assert peers                        # still returns a (sector) peer set, never empty


def test_derive_peers_falls_back_when_sic_lookup_returns_none():
    uni = _universe()
    peers, source = pl.derive_peers(uni, "AAA", k=5, sic_of=lambda t: None)
    assert source == "sector"
    assert peers


# --------------------------------------------------------------------------- #
# End-to-end funnel (monkeypatched engines, no network)
# --------------------------------------------------------------------------- #

@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    comps = make_model_inputs().comps

    def fake_screener(tickers=None, **kw):
        return _screened([t.upper() for t in (tickers or ["AAA", "BBB", "CCC"])])

    def fake_run_comps(tk, peers=None, *, output_path=None, **kw):
        if output_path:
            from openpyxl import Workbook
            Workbook().save(output_path)            # real, openable stub artifact
            Path(output_path).with_suffix(".md").write_text("stub", encoding="utf-8")
        return comps

    def fake_build_models(tk, peers=None, *, output_path=None, comps_result=None, **kw):
        from openpyxl import Workbook
        Workbook().save(output_path)
        return ModelBuildResult(ticker=tk, xlsx_path=Path(output_path), is_bank=False,
                                has_sotp=False, comps_result=comps_result or comps)

    monkeypatch.setattr(pl, "load_universe", _universe)
    monkeypatch.setattr(pl, "run_screener", fake_screener)
    monkeypatch.setattr(pl, "run_comps", fake_run_comps)
    monkeypatch.setattr(pl, "build_models", fake_build_models)
    monkeypatch.setattr(pl, "cache_dir", lambda: tmp_path)
    # SIC-aware peer derivation calls EdgarProvider().get_sic — stub it offline.
    monkeypatch.setattr(pl.EdgarProvider, "get_sic", lambda self, t, **kw: None)
    return tmp_path


def test_pipeline_topn_writes_dossiers_index_manifest(stubbed):
    res = pl.run_pipeline(top=2)
    assert res.shortlist == ["AAA", "BBB"]
    # Per-name dossiers with both artifacts.
    for tk in res.shortlist:
        assert (res.out_dir / tk / f"comps_{tk}.xlsx").exists()
        assert (res.out_dir / tk / f"{tk}_model.xlsx").exists()
    # Ranked index + manifest.
    assert res.index_xlsx.exists() and res.index_md.exists()
    manifest = json.loads(res.manifest_path.read_text())
    assert manifest["shortlist"] == ["AAA", "BBB"]
    assert manifest["params"]["top"] == 2
    # Index carries the seeded valuation read (MoS from comps).
    d = res.dossiers[0]
    assert d.margin_of_safety_base is not None
    assert d.peers_used   # auto-derived from the universe


def test_pipeline_explicit_tickers_keep_all_and_autopeer(stubbed):
    res = pl.run_pipeline(tickers=["aaa", "ccc"], top=10)
    assert res.shortlist == ["AAA", "CCC"]
    # CCC (8bn industrials) auto-derives a peer set from the universe.
    ccc = [d for d in res.dossiers if d.ticker == "CCC"][0]
    assert ccc.peers_used


def test_pipeline_sycamore_only_filter(stubbed):
    res = pl.run_pipeline(sycamore_only=True, top=10)
    # Only AAA is Sycamore-owned AND industrials-screened in the stub.
    assert "AAA" in res.shortlist
    assert "BBB" not in res.shortlist


def test_pipeline_no_universe_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(pl, "load_universe", lambda: None)

    def _no_csvs():
        raise FileNotFoundError("No holdings CSVs found in data/raw/.")

    monkeypatch.setattr(pl, "build_universe", _no_csvs)
    with pytest.raises(FileNotFoundError, match="explicit tickers"):
        pl.run_pipeline(sector="industrials")

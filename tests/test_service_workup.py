"""NameWorkupView: bear-first DCF, downside flags, bands, normalized/trough,
model link-only, vol note when off."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from sycamore_prep.comps.comps import CompsResult, TickerAnalysis
from sycamore_prep.comps.history import BandSummary, HistoryResult
from sycamore_prep.comps.normalized import NormalizedEarnings
from sycamore_prep.comps.reverse_dcf import DcfCase
from sycamore_prep.service import workup as svc
from sycamore_prep.service.viewmodels import ArtifactRef


def _case(label, mos, fv):
    return DcfCase(label=label, wacc=0.09, terminal_growth=0.025, fcf0=100.0,
                   implied_growth=0.05, implied_converged=True, assumed_growth=0.03,
                   fair_value_per_share=fv, current_price=50.0, margin_of_safety=mos)


def _comps_result(wl_root):
    bands = {
        "PE": BandSummary("PE", 12.0, 8, 65.0, 8.0, 10.0, 14.0, 18.0, 22.0, True,
                          pd.Series([10.0, 14.0])),
        "FCF_Yield": BandSummary("FCF_Yield", 0.08, 8, 70.0, 0.02, 0.04, 0.06, 0.09,
                                 0.12, False, pd.Series([0.04, 0.08])),
    }
    hist = HistoryResult(ticker="CW", share_basis="wad", is_bank=False,
                         fy_end_prices=pd.Series([40.0, 50.0]),
                         market_caps=pd.Series([4e9, 5e9]), bands=bands)
    norm = NormalizedEarnings(window=10, n_used=8, mean_op_margin=0.15,
                              trough_op_margin=0.08, current_revenue=1000.0,
                              interest_expense=20.0, normalized_op_income=150.0,
                              normalized_net_income=110.0, normalized_eps=4.0,
                              trough_net_income=60.0, trough_eps=2.0, normalized_pe=12.5,
                              trough_pe=25.0, trailing_pe=15.0, tax_rate=0.21, basis="wad")
    subj = TickerAnalysis(
        ticker="CW", name="Curtiss-Wright", is_bank=False, market_cap=5e9, price=50.0,
        current={"pe": 12.0, "ev_ebitda": 9.0, "fcf_yield": 0.08, "p_tbv": 3.0},
        quality={"roic": 0.18, "fcf_margin": 0.12, "net_debt_ebitda": 4.0},
        history=hist, normalized=norm,
        dcf_cases=[_case("bull", 0.4, 80.0), _case("bear", -0.2, 40.0), _case("base", 0.1, 55.0)],
        sources="edgar (primary), yfinance (non-primary)", error=None,
    )
    xlsx = wl_root.cache / "comps_CW.xlsx"
    xlsx.write_text("x")
    md = wl_root.cache / "comps_CW.md"
    md.write_text("x")
    return CompsResult(subject=subj, peers=[], peer_table=pd.DataFrame(), wacc=0.09,
                       terminal_growth=0.025, forecast_years=10, share_basis="wad",
                       xlsx_path=xlsx, md_path=md)


@pytest.fixture
def view(wl_root, monkeypatch):
    monkeypatch.setattr(svc, "run_comps", lambda *a, **k: _comps_result(wl_root))
    return svc.for_ticker("CW")


def test_dcf_cases_ordered_bear_base_bull(view):
    assert [c.label for c in view.dcf_cases] == ["bear", "base", "bull"]


def test_negative_mos_flagged_risk(view):
    assert view.dcf_cases[0].margin_of_safety.value == -0.2
    assert view.dcf_cases[0].margin_of_safety.flag == "risk"
    assert view.dcf_cases[1].margin_of_safety.flag is None    # base positive


def test_headline_downside_and_normalized(view):
    assert view.base_margin_of_safety is not None
    assert view.normalized.trough_pe.value == 25.0 and view.normalized.trough_pe.flag == "warn"
    assert view.normalized.trough_eps.flag == "warn"


def test_bands_lower_is_cheap_preserved(view):
    pe = next(b for b in view.bands if b.multiple == "PE")
    fy = next(b for b in view.bands if b.multiple == "FCF_Yield")
    assert pe.lower_is_cheap is True
    assert fy.lower_is_cheap is False


def test_model_link_only_and_comps_downloads(view):
    assert view.model_artifact is None          # no <T>_model.xlsx -> build is Phase B
    assert isinstance(view.comps_artifacts["xlsx"], ArtifactRef)
    assert view.comps_artifacts["xlsx"].kind == "xlsx"
    assert isinstance(view.comps_artifacts["md"], ArtifactRef)


def test_vol_panel_note_when_off(view):
    assert view.vol_panel is not None
    assert view.vol_panel.note and "vol overlay off" in view.vol_panel.note


def test_workup_json_safe(view):
    json.loads(view.model_dump_json())

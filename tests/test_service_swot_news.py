"""Auto-SWOT (deterministic, from the numbers) + the NON-PRIMARY news panel."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from sycamore_prep.comps.comps import CompsResult, TickerAnalysis
from sycamore_prep.comps.normalized import NormalizedEarnings
from sycamore_prep.comps.reverse_dcf import DcfCase
from sycamore_prep.service import news as news_svc
from sycamore_prep.service import workup as svc
from sycamore_prep.service.viewmodels import NewsView, SwotView


def _case(label, mos, fv):
    return DcfCase(label=label, wacc=0.09, terminal_growth=0.025, fcf0=100.0,
                   implied_growth=0.04, implied_converged=True, assumed_growth=0.03,
                   fair_value_per_share=fv, current_price=50.0, margin_of_safety=mos)


def _comps(*, roic, lev, mos, trough_pe):
    norm = NormalizedEarnings(window=10, n_used=8, mean_op_margin=0.15, trough_op_margin=0.08,
                              current_revenue=1000.0, interest_expense=20.0, normalized_op_income=150.0,
                              normalized_net_income=110.0, normalized_eps=4.0, trough_net_income=60.0,
                              trough_eps=2.0, normalized_pe=12.5, trough_pe=trough_pe, trailing_pe=15.0,
                              tax_rate=0.21, basis="wad")
    subj = TickerAnalysis(
        ticker="CW", name="Curtiss-Wright", is_bank=False, market_cap=5e9, price=50.0,
        current={"pe": 12.0}, quality={"roic": roic, "fcf_margin": 0.12, "net_debt_ebitda": lev},
        normalized=norm,
        dcf_cases=[_case("bear", mos - 0.3, 40.0), _case("base", mos, 55.0), _case("bull", mos + 0.3, 80.0)],
        sources="edgar (primary)", error=None)
    return CompsResult(subject=subj, peers=[], peer_table=pd.DataFrame(), wacc=0.09,
                       terminal_growth=0.025, forecast_years=10, share_basis="wad")


@pytest.fixture
def good(wl_root, monkeypatch):
    monkeypatch.setattr(svc, "run_comps", lambda *a, **k: _comps(roic=0.18, lev=1.0, mos=0.25, trough_pe=14.0))
    return svc.for_ticker("CW")


@pytest.fixture
def bad(wl_root, monkeypatch):
    monkeypatch.setattr(svc, "run_comps", lambda *a, **k: _comps(roic=0.05, lev=4.0, mos=-0.20, trough_pe=30.0))
    return svc.for_ticker("CW")


def test_swot_present_and_json_safe(good):
    assert isinstance(good.swot, SwotView)
    assert good.swot.headline and good.swot.summary
    json.loads(good.model_dump_json())


def test_strong_cheap_name_reads_as_strength_and_opportunity(good):
    s = good.swot
    assert any("returns on capital" in i.text.lower() for i in s.strengths)
    assert any("conservative balance sheet" in i.text.lower() for i in s.strengths)
    assert any("margin of safety" in i.text.lower() for i in s.opportunities)
    assert "margin of safety" in s.headline.lower()
    # Every bullet that cites a number carries it (auditable).
    assert all(i.metric is not None for i in s.strengths)


def test_weak_expensive_name_reads_as_weakness_and_threat(bad):
    s = bad.swot
    assert any("leverage" in i.text.lower() for i in s.weaknesses)
    assert any("above estimated fair value" in i.text.lower() for i in s.threats)
    # Downside MoS bullet is flagged risk.
    above = next(i for i in s.threats if "above estimated fair value" in i.text.lower())
    assert above.metric.flag == "risk"
    assert "above fair value" in s.headline.lower()


def test_news_view_from_provider(monkeypatch):
    df = pd.DataFrame([
        {"title": "CW wins defense contract", "publisher": "Reuters",
         "link": "http://x/1", "published": "2026-05-30T10:00:00Z", "ticker": "CW",
         "source": "yfinance (non-primary)"},
        {"title": "CW raises guidance", "publisher": "Bloomberg",
         "link": "http://x/2", "published": 1717000000, "ticker": "CW",
         "source": "yfinance (non-primary)"},
    ])
    monkeypatch.setattr(news_svc.YFinanceProvider, "get_news", lambda self, t, limit=8: df)
    v = news_svc.for_ticker("CW")
    assert isinstance(v, NewsView) and len(v.items) == 2
    assert v.items[0].title == "CW wins defense contract"
    assert v.items[0].published == "2026-05-30"          # ISO trimmed
    assert v.items[1].published == "2024-05-29"          # epoch -> date
    assert v.note is None
    json.loads(v.model_dump_json())


def test_news_degrades_without_network(monkeypatch):
    def boom(self, t, limit=8):
        raise RuntimeError("offline")
    monkeypatch.setattr(news_svc.YFinanceProvider, "get_news", boom)
    v = news_svc.for_ticker("CW")
    assert v.items == [] and v.note and "Couldn't fetch" in v.note   # clear note, no crash

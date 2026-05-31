"""ScreenerView: three attributes separate, source tags, overlay segregation,
NaN-safety, downside flags."""

from __future__ import annotations

import json

import pytest

from sycamore_prep.service import screener as svc
from sycamore_prep.service.viewmodels import Figure, ScreenerRowView


@pytest.fixture
def view(screener_frame, monkeypatch):
    monkeypatch.setattr(svc, "run_screener", lambda **kw: screener_frame)
    return svc.run(tickers=["AAA", "BBB"], with_vol=True, tv_overlay=True,
                   with_prediction_overlay=True)


def test_three_attributes_kept_separate(view):
    row = next(r for r in view.rows if r.ticker == "AAA")
    assert isinstance(row.q1_quality, Figure) and row.q1_quality.value == 80.0
    assert isinstance(row.q2_valuation, Figure) and row.q2_valuation.value == 70.0
    assert isinstance(row.q3_improving, Figure) and row.q3_improving.value == 60.0
    assert row.composite_rank == 1 and row.rank_role == "sort_key_only"
    # No collapsed "overall"/"combined" quality field on the row model.
    fields = set(ScreenerRowView.model_fields)
    assert not any(("overall" in f or "combined" in f) for f in fields)


def test_source_tags_present_and_primary_flagged(view):
    row = next(r for r in view.rows if r.ticker == "AAA")
    assert row.q1_quality.source and row.q1_quality.primary is True        # edgar
    assert row.q2_valuation.primary is False                              # price-spliced
    assert row.market_cap.source and row.market_cap.primary is False      # yfinance
    assert "edgar (primary)" in row.sources


def test_overlay_columns_segregated_to_the_right(view):
    assert view.overlay_columns["vol"]
    assert view.overlay_columns["tv"]
    assert view.overlay_columns["prediction"]
    assert view.overlays_active == {"vol": True, "tv": True, "prediction": True}
    order = view.column_order
    i_flags = order.index("ns_flags")
    overlay_cols = (view.overlay_columns["vol"] + view.overlay_columns["tv"]
                    + view.overlay_columns["prediction"])
    assert all(order.index(c) > i_flags for c in overlay_cols)


def test_rank_unchanged_claim_and_notes(view):
    assert view.rank_unchanged.guaranteed is True
    assert "screen.py" in view.rank_unchanged.citation
    assert view.notes["tv"] and "skipped" in view.notes["tv"]


def test_nan_serializes_to_null_and_view_is_json_safe(view):
    bbb = next(r for r in view.rows if r.ticker == "BBB")
    assert bbb.market_cap.value is None
    assert bbb.q3_improving.value is None
    json.loads(view.model_dump_json())          # strict JSON across the whole view


def test_negative_space_row_flagged(view):
    bbb = next(r for r in view.rows if r.ticker == "BBB")
    assert bbb.negative_space is True
    assert "low_or_neg_fcf" in bbb.ns_flags
    assert bbb.flag == "risk"

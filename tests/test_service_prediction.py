"""MappingView (display) + confirm write-back preserving human edits."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.prediction_markets import mapping as mp
from sycamore_prep.service import prediction as svc


def _seed(path):
    seed = pd.DataFrame([{
        "ticker": "CW", "aperture": "company", "slug": "cw-merger-2026",
        "question": "Will CW be acquired in 2026?", "event_type": "m&a",
        "direction": "risk", "relevance_score": 0.8, "confirmed": False, "notes": "",
    }])
    mp.save_mapping(seed, path)


def test_mapping_view_empty(tmp_path):
    v = svc.mapping_view(tmp_path / "prediction_markets.csv")
    assert v.rows == [] and v.note and "No prediction-market" in v.note
    assert v.editable is False


def test_confirm_marks_human_owned_and_counts(tmp_path):
    p = tmp_path / "prediction_markets.csv"
    _seed(p)
    v = svc.confirm([{"ticker": "CW", "slug": "cw-merger-2026",
                      "confirmed": True, "notes": "watch closely"}], p)
    row = v.rows[0]
    assert row.confirmed is True and row.human_owned is True
    assert row.notes == "watch closely"
    assert v.counts["confirmed"] == 1 and v.counts["annotated"] == 1
    assert v.editable is True
    assert row.relevance_score.primary is False     # polymarket non-primary


def test_human_edits_survive_rediscovery(tmp_path):
    p = tmp_path / "prediction_markets.csv"
    _seed(p)
    svc.confirm([{"ticker": "CW", "slug": "cw-merger-2026",
                  "confirmed": True, "notes": "keep"}], p)
    # Re-discovery returns DIFFERENT candidates for the same (ticker, aperture).
    new = pd.DataFrame([{
        "ticker": "CW", "aperture": "company", "slug": "cw-new-litigation",
        "question": "New suit?", "event_type": "litigation", "direction": "risk",
        "relevance_score": 0.6,
    }])
    mp.save_mapping(mp.upsert(mp.load_mapping(p), new), p)

    rows = {r.slug: r for r in svc.mapping_view(p).rows}
    assert "cw-merger-2026" in rows                       # confirmed row NOT pruned
    assert rows["cw-merger-2026"].confirmed is True
    assert rows["cw-merger-2026"].notes == "keep"
    assert "cw-new-litigation" in rows                    # new candidate added

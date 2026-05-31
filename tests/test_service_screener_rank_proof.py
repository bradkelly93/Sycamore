"""The §3.4 dual-run proof: toggling overlays never moves composite_rank."""

from __future__ import annotations

from sycamore_prep.service import screener as svc

_OVERLAY_COLS = ("iv_rank", "iv_percentile", "iv_index", "expected_move_30d_pct",
                 "vol_flags", "vol_source", "passes_screen", "tv_divergence",
                 "event_top_prob", "event_contradiction")


def test_verify_rank_unchanged_identical(screener_frame, monkeypatch, wl_root):
    """Overlays-ON merely appends columns; ranks are identical to overlays-OFF."""
    def fake(**kw):
        df = screener_frame.copy()
        if not (kw.get("with_vol") or kw.get("tv_overlay") or kw.get("with_prediction_overlay")):
            df = df.drop(columns=[c for c in _OVERLAY_COLS if c in df.columns])
        return df
    monkeypatch.setattr(svc, "run_screener", fake)

    proof = svc.verify_rank_unchanged(tickers=["AAA", "BBB"])
    assert proof.identical is True
    assert {r["ticker"] for r in proof.rows} == {"AAA", "BBB"}
    assert all(r["rank_overlays_off"] == r["rank_overlays_on"] for r in proof.rows)
    assert set(proof.overlays_checked) == {"vol", "tv", "prediction"}


def test_verify_detects_a_regression(screener_frame, monkeypatch, wl_root):
    """If overlays DID change the rank, the proof must catch it (identical=False)."""
    def fake(**kw):
        df = screener_frame.copy()
        if kw.get("with_vol") or kw.get("tv_overlay") or kw.get("with_prediction_overlay"):
            df["composite_rank"] = [2, 1]   # a fault the proof should surface
        return df
    monkeypatch.setattr(svc, "run_screener", fake)

    proof = svc.verify_rank_unchanged(tickers=["AAA", "BBB"])
    assert proof.identical is False

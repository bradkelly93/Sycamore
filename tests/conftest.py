"""Shared fixtures for the service-layer tests (additive — no ``autouse``, so the
existing engine suite is untouched).

The service tests exercise the *translation* layer (engine return -> JSON-safe
view-model), not the engine analytics (already covered). So they monkeypatch the
engine entry points the service wraps, and redirect the artifact whitelist roots
to a tmp dir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sycamore_prep.service import artifacts as svc_artifacts
from sycamore_prep.service import pipeline as svc_pipeline
from sycamore_prep.service import screener as svc_screener
from sycamore_prep.service import universe as svc_universe
from sycamore_prep.service import workup as svc_workup


class _Roots:
    def __init__(self, cache, models, raw):
        self.cache = cache
        self.models = models
        self.raw = raw


@pytest.fixture
def wl_root(tmp_path, monkeypatch):
    """Redirect cache_dir / models_dir / raw_dir (the artifact whitelist roots and
    where the engines write) to an isolated tmp tree, across every service module
    that imported them by name."""
    cache = tmp_path / "cache"
    models = tmp_path / "models"
    raw = tmp_path / "raw"
    for d in (cache, models, raw):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(svc_artifacts, "cache_dir", lambda: cache)
    monkeypatch.setattr(svc_artifacts, "models_dir", lambda: models)
    monkeypatch.setattr(svc_artifacts, "raw_dir", lambda: raw)
    monkeypatch.setattr(svc_pipeline, "cache_dir", lambda: cache)
    monkeypatch.setattr(svc_universe, "cache_dir", lambda: cache)
    monkeypatch.setattr(svc_screener, "cache_dir", lambda: cache)
    monkeypatch.setattr(svc_workup, "models_dir", lambda: models)
    return _Roots(cache, models, raw)


@pytest.fixture
def screener_frame():
    """A synthetic screener-output frame shaped like ``run_screener`` returns:
    indexed by ticker, three separate sub-scores, negative-space flags, mixed
    sources, NaNs, and all three NON-PRIMARY overlay column groups (to the right
    of the downside flags). Returns a fresh copy so tests can mutate freely."""
    df = pd.DataFrame(
        {
            "name": ["Alpha Inc", "Beta Corp"],
            "gics_sector": ["Industrials", "Financials"],
            "is_bank": [False, True],
            "market_cap": [1.2e9, np.nan],
            "composite_rank": [1, 2],
            "composite_score": [78.0, 41.0],
            "q1_quality_score": [80.0, 35.0],
            "q2_valuation_score": [70.0, 50.0],
            "q3_improving_score": [60.0, np.nan],
            "negative_space": [False, True],
            "ns_flags": ["", "low_or_neg_fcf, extreme_pe"],
            "roic": [0.18, 0.04],
            "pe": [12.0, 25.0],
            "fcf_yield": [0.08, 0.02],
            "sources": ["edgar (primary), yfinance (non-primary)"] * 2,
            "error": [None, None],
            # vol overlay (right of the downside flags)
            "iv_rank": [55.0, np.nan],
            "iv_percentile": [60.0, np.nan],
            "iv_index": [0.30, np.nan],
            "expected_move_30d_pct": [0.08, np.nan],
            "vol_flags": ["high_iv_rank", ""],
            "vol_source": ["tastytrade", ""],
            # technical overlay
            "passes_screen": [True, False],
            "tv_divergence": ["agree_strong", "no_tv"],
            # prediction overlay
            "event_top_prob": [0.15, np.nan],
            "event_contradiction": [False, False],
        },
        index=pd.Index(["AAA", "BBB"], name="ticker"),
    )
    df.attrs["vol_note"] = None
    df.attrs["tv_note"] = "tv overlay skipped: disabled in config"
    df.attrs["prediction_note"] = None
    return df

"""Serialization boundary + artifact-token security."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from sycamore_prep.service import artifacts, serde
from sycamore_prep.service.viewmodels import ArtifactRef


def test_clean_scalar_nan_inf_nat_to_none():
    assert serde.clean_scalar(np.float64("nan")) is None
    assert serde.clean_scalar(float("inf")) is None
    assert serde.clean_scalar(float("-inf")) is None
    assert serde.clean_scalar(pd.NaT) is None
    assert serde.clean_scalar(np.nan) is None
    assert serde.clean_scalar(None) is None


def test_clean_scalar_numpy_to_python():
    v = serde.clean_scalar(np.int64(7))
    assert v == 7 and type(v) is int
    v = serde.clean_scalar(np.float64(2.5))
    assert v == 2.5 and type(v) is float
    v = serde.clean_scalar(np.bool_(True))
    assert v is True and type(v) is bool
    assert serde.clean_scalar("hi") == "hi"


def test_df_to_records_orders_and_cleans(screener_frame):
    recs = serde.df_to_records(
        screener_frame, column_order=["ticker", "composite_rank", "q1_quality_score"]
    )
    assert recs[0]["ticker"] == "AAA"          # named index surfaced as a column
    assert list(recs[0])[:3] == ["ticker", "composite_rank", "q1_quality_score"]
    bbb = next(r for r in recs if r["ticker"] == "BBB")
    assert bbb["market_cap"] is None            # NaN -> None
    json.loads(json.dumps(recs))                # strict JSON, no NaN token


def test_split_flags():
    assert serde.split_flags("a, b ,c,") == ["a", "b", "c"]
    assert serde.split_flags(["x", " y "]) == ["x", "y"]
    assert serde.split_flags(np.nan) == []
    assert serde.split_flags(None) == []


def test_split_sources_respects_parentheses():
    # A tag like "trend-proxy (non-primary, technical)" has a comma inside its
    # parens and must NOT be split there.
    s = "edgar (primary), yfinance (non-primary), trend-proxy (non-primary, technical)"
    assert serde.split_sources(s) == [
        "edgar (primary)",
        "yfinance (non-primary)",
        "trend-proxy (non-primary, technical)",
    ]
    assert serde.split_sources(np.nan) == []
    assert serde.split_sources(["a", "b"]) == ["a", "b"]


def test_artifact_register_resolve_roundtrip(wl_root):
    f = wl_root.cache / "thing.xlsx"
    f.write_text("x")
    ref = artifacts.register(f)
    assert isinstance(ref, ArtifactRef)
    assert ref.filename == "thing.xlsx" and ref.kind == "xlsx"
    assert "/" not in ref.token and ".." not in ref.token   # opaque token
    assert artifacts.resolve(ref.token) == f.resolve()


def test_artifact_register_rejects_outside_whitelist(wl_root, tmp_path):
    evil = tmp_path / "outside.txt"
    evil.write_text("x")
    with pytest.raises(ValueError):
        artifacts.register(evil)


def test_artifact_register_rejects_traversal(wl_root):
    with pytest.raises(ValueError):
        artifacts.register(wl_root.cache / ".." / ".." / "etc" / "passwd")


def test_artifact_resolve_rejects_forged_token(wl_root):
    with pytest.raises(KeyError):
        artifacts.resolve("totally-made-up-token")

"""Unit tests for the EDGAR XBRL parser. Exercises a tiny synthetic
companyfacts payload — no network."""

from __future__ import annotations

import pandas as pd

from sycamore_prep.adapters.edgar import _parse_company_facts, CANONICAL_CONCEPTS


def _facts(payload: dict) -> dict:
    return {"facts": {"us-gaap": payload}}


def test_parser_picks_first_matching_tag_and_dedupes_restatements():
    facts = _facts({
        "Revenues": {
            "units": {
                "USD": [
                    # FY2022 has a restatement — later `filed` should win.
                    {"end": "2022-12-31", "val": 100, "fy": 2022, "fp": "FY",
                     "form": "10-K", "filed": "2023-02-15"},
                    {"end": "2022-12-31", "val": 105, "fy": 2022, "fp": "FY",
                     "form": "10-K", "filed": "2024-02-20"},
                    {"end": "2023-12-31", "val": 120, "fy": 2023, "fp": "FY",
                     "form": "10-K", "filed": "2024-02-20"},
                ]
            }
        },
        "NetIncomeLoss": {
            "units": {"USD": [
                {"end": "2023-12-31", "val": 15, "fy": 2023, "fp": "FY",
                 "form": "10-K", "filed": "2024-02-20"},
            ]}
        },
    })
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)

    rev_fy = df[(df["concept"] == "Revenues") & (df["fp"] == "FY")].sort_values("period")
    assert list(rev_fy["value"]) == [105.0, 120.0], "Restatement should win on later `filed`."

    ni = df[df["concept"] == "NetIncomeLoss"]
    assert ni.iloc[0]["value"] == 15.0
    assert (df["source"] == "edgar (primary)").all()


def test_parser_falls_back_through_tag_synonyms():
    # Use a synonym for Revenues — pure tag name not present, but synonym is.
    facts = _facts({
        "SalesRevenueNet": {
            "units": {"USD": [
                {"end": "2023-12-31", "val": 999, "fy": 2023, "fp": "FY",
                 "form": "10-K", "filed": "2024-02-20"},
            ]}
        }
    })
    df = _parse_company_facts("XYZ", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"]
    assert len(rev) == 1
    assert rev.iloc[0]["value"] == 999.0


def test_parser_returns_empty_frame_when_no_canonical_match():
    facts = _facts({"SomeRandomTag": {"units": {"USD": [{"end": "2023-12-31", "val": 1}]}}})
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)
    assert df.empty
    # Schema is preserved.
    for col in ["ticker", "concept", "period", "value", "unit", "source"]:
        assert col in df.columns


def test_parser_keeps_every_period_from_a_multi_year_filing():
    """A 10-K reports 3 years of comparatives — all three entries carry the
    SAME `fy` (the filing's fy). The earlier (fy, fp, form) dedupe collapsed
    them into one arbitrary survivor; the fixed (end, fp, form) dedupe keeps
    one row per period. This is the root cause of the off-by-period bug Brad
    surfaced when CW NetIncomeLoss showed 2021 values labeled as FY2023."""
    facts = _facts({
        "Revenues": {
            "units": {"USD": [
                # All three from the FY2024 10-K (fy=2024).
                {"end": "2022-12-31", "val": 100, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2025-02-15"},
                {"end": "2023-12-31", "val": 120, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2025-02-15"},
                {"end": "2024-12-31", "val": 140, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2025-02-15"},
            ]}
        }
    })
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"].sort_values("period")
    assert list(rev["period"]) == ["2022-12-31", "2023-12-31", "2024-12-31"]
    assert list(rev["value"]) == [100.0, 120.0, 140.0]


def test_parser_dedupes_restatements_within_a_period():
    """Two filings report end=2022-12-31: original then restated. Keep latest."""
    facts = _facts({
        "Revenues": {
            "units": {"USD": [
                {"end": "2022-12-31", "val": 100, "fy": 2022, "fp": "FY",
                 "form": "10-K", "filed": "2023-02-15"},
                {"end": "2022-12-31", "val": 108, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2025-02-15"},
            ]}
        }
    })
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"]
    assert len(rev) == 1
    assert rev.iloc[0]["value"] == 108.0


def test_synonym_picker_prefers_tag_with_most_periods():
    """When two synonyms both have data, pick the richer one. Previously
    'first synonym wins' caused D&A to come back as a single 2009 row because
    the first candidate had one stale entry while the third had every year."""
    facts = _facts({
        # First in the candidate list — but only one stale year.
        "DepreciationDepletionAndAmortization": {
            "units": {"USD": [
                {"end": "2009-12-31", "val": 50, "fy": 2009, "fp": "FY",
                 "form": "10-K", "filed": "2010-02-15"},
            ]}
        },
        # Third in the candidate list — five recent years. Should win.
        "DepreciationAndAmortization": {
            "units": {"USD": [
                {"end": f"{y}-12-31", "val": 100 + i * 10, "fy": y, "fp": "FY",
                 "form": "10-K", "filed": f"{y + 1}-02-15"}
                for i, y in enumerate([2020, 2021, 2022, 2023, 2024])
            ]}
        },
    })
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)
    da = df[df["concept"] == "DepreciationAndAmortization"].sort_values("period")
    assert len(da) == 5
    assert list(da["period"]) == [
        "2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31",
    ]

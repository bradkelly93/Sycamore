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

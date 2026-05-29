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


def test_parser_captures_period_start_and_days():
    """The parser must record `start` and `period_days` so downstream code
    can distinguish annual flows (~365d) from mis-tagged quarterly (~91d)."""
    facts = _facts({
        "Revenues": {
            "units": {"USD": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 3000,
                 "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-15"},
            ]}
        },
        "Assets": {
            # Instant concept — no `start`.
            "units": {"USD": [
                {"end": "2024-12-31", "val": 50000, "fy": 2024, "fp": "FY",
                 "form": "10-K", "filed": "2025-02-15"},
            ]}
        }
    })
    df = _parse_company_facts("ABC", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"].iloc[0]
    assert rev["period_start"] == "2024-01-01"
    assert rev["period_days"] == 365 or rev["period_days"] == 366
    assets = df[df["concept"] == "Assets"].iloc[0]
    assert assets["period_start"] is None or pd.isna(assets["period_start"])
    assert assets["period_days"] is None or pd.isna(assets["period_days"])


def test_synonym_picker_counts_only_annual_periods():
    """The legacy 'Revenues' tag for CW had 12 quarterly interim entries
    (2015 Q1 through 2017 Q4) plus 3 FY annuals. The post-2018 ASC 606 tag
    had 8 clean FY annuals. Picking by raw entry count chose the legacy
    tag and missed every recent fiscal year. Annual-period count fixes it."""
    facts = _facts({
        "Revenues": {
            "units": {"USD": [
                # 3 annual entries (clean)…
                {"start": "2015-01-01", "end": "2015-12-31", "val": 2200,
                 "fy": 2015, "fp": "FY", "form": "10-K", "filed": "2016-02-15"},
                {"start": "2016-01-01", "end": "2016-12-31", "val": 2100,
                 "fy": 2016, "fp": "FY", "form": "10-K", "filed": "2017-02-15"},
                {"start": "2017-01-01", "end": "2017-12-31", "val": 2300,
                 "fy": 2017, "fp": "FY", "form": "10-K", "filed": "2018-02-15"},
                # …plus 6 quarterly interim entries mis-tagged fp=FY.
                *[
                    {"start": s, "end": e, "val": 550, "fy": fy, "fp": "FY",
                     "form": "10-K", "filed": "2018-02-15"}
                    for s, e, fy in [
                        ("2015-01-01", "2015-03-31", 2015),
                        ("2015-04-01", "2015-06-30", 2015),
                        ("2015-07-01", "2015-09-30", 2015),
                        ("2016-01-01", "2016-03-31", 2016),
                        ("2016-04-01", "2016-06-30", 2016),
                        ("2016-07-01", "2016-09-30", 2016),
                    ]
                ],
            ]}
        },
        # 8 clean annuals — must win even though raw entry count is lower.
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [
                {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 2500 + (y - 2018) * 100,
                 "fy": y, "fp": "FY", "form": "10-K", "filed": f"{y + 1}-02-15"}
                for y in range(2018, 2026)
            ]}
        }
    })
    df = _parse_company_facts("CW", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"].sort_values("period")
    # Should have picked the post-2018 tag — last period should be 2025.
    assert rev["period"].iloc[-1] == "2025-12-31"
    assert rev["value"].iloc[-1] == 2500 + 7 * 100  # 3200


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


def test_synonym_picker_prefers_current_tag_over_longer_deprecated_one():
    """The exact CW/CACI bug. A deprecated ASC-605 tag carries the LONGEST run
    of annual periods (SalesRevenueNet, 2008-2017 = 10 years) while the current
    ASC-606 tag has fewer but more-recent annuals (2021-2025 = 5). Ranking by
    raw annual COUNT locked onto the stale tag and the canonical series ended
    at 2017. The picker must prefer the tag covering the most recent fiscal
    year even though it has fewer annual periods, so the series stays on the
    live accounting basis."""
    facts = _facts({
        # Deprecated tag — 10 annual ends, but stops at 2017.
        "SalesRevenueNet": {
            "units": {"USD": [
                {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 1000 + y,
                 "fy": y, "fp": "FY", "form": "10-K", "filed": f"{y + 1}-02-15"}
                for y in range(2008, 2018)
            ]}
        },
        # Current tag — only 5 annual ends, but reaches 2025. Must win.
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [
                {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 2500 + y,
                 "fy": y, "fp": "FY", "form": "10-K", "filed": f"{y + 1}-02-15"}
                for y in range(2021, 2026)
            ]}
        },
    })
    df = _parse_company_facts("CW", facts, CANONICAL_CONCEPTS)
    rev = df[df["concept"] == "Revenues"].sort_values("period")
    assert rev["period"].iloc[-1] == "2025-12-31"      # live basis, not 2017
    assert rev["period"].iloc[0] == "2021-12-31"        # single consistent tag
    assert rev["value"].iloc[-1] == 2500 + 2025         # 4525
    assert len(rev) == 5

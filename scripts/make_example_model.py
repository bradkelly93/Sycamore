"""Generate the committed SYNTHETIC example scaffold.

Writes ``models/templates/example_synthetic_model.xlsx`` — a non-bank workbook
built from hand-made (NOT pulled) numbers, so it can be committed without
shipping real fundamentals. It documents the tab/formula layout; it is NOT a
real model. Regenerate with:  ``python3 scripts/make_example_model.py``

Real, data-backed scaffolds are produced locally with
``python -m sycamore_prep.cli build-models <TICKER>`` and land in
``models/<TICKER>_model.xlsx`` (gitignored).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from sycamore_prep.comps.comps import CompsResult, TickerAnalysis
from sycamore_prep.comps.history import BandSummary, HistoryResult
from sycamore_prep.comps.normalized import NormalizedEarnings
from sycamore_prep.comps.reverse_dcf import DcfCase
from sycamore_prep.config import project_root
from sycamore_prep.models import ModelInputs, build_workbook

_M = 1_000_000


def _band(name, current, p25, median, p75, lower=True):
    return BandSummary(name, current, 6, 50.0, p25 * 0.9, p25, median, p75, p75 * 1.1, lower)


def synthetic_inputs() -> ModelInputs:
    bands = {
        "PE": _band("PE", 15.0, 12.0, 15.0, 18.0),
        "EV_EBITDA": _band("EV_EBITDA", 9.0, 7.0, 9.0, 11.0),
        "FCF_Yield": _band("FCF_Yield", 0.05, 0.03, 0.05, 0.07, lower=False),
    }
    dcf = [
        DcfCase("bear", 0.10, 0.020, 240 * _M, 0.02, True, 0.04, 48.0, 60.0, -0.20),
        DcfCase("base", 0.09, 0.025, 300 * _M, 0.05, True, 0.04, 70.0, 60.0, 0.1667),
        DcfCase("bull", 0.08, 0.030, 320 * _M, 0.08, True, 0.04, 95.0, 60.0, 0.583),
    ]
    norm = NormalizedEarnings(
        window=5, n_used=5, mean_op_margin=0.16, trough_op_margin=0.10,
        current_revenue=1500 * _M, interest_expense=20 * _M,
        normalized_op_income=240 * _M, normalized_net_income=173.8 * _M,
        normalized_eps=6.95, trough_net_income=102.2 * _M, trough_eps=4.09,
        normalized_pe=8.63, trough_pe=14.67, trailing_pe=10.0, tax_rate=0.21,
        basis="synthetic example")
    hist = HistoryResult("EXAMPLE", "wad", False, pd.Series(dtype=float),
                         pd.Series(dtype=float), bands)
    peer_table = pd.DataFrame({
        "pe": {"EXAMPLE": 15.0, "PEER1": 22.0, "PEER2": 26.0,
               "PEER_MEDIAN": 24.0, "PEER_MEAN": 24.0},
        "name": {k: k for k in ["EXAMPLE", "PEER1", "PEER2", "PEER_MEDIAN", "PEER_MEAN"]},
    })
    subject = TickerAnalysis(
        ticker="EXAMPLE", name="Example Industrials (SYNTHETIC)", is_bank=False,
        market_cap=2300 * _M, price=60.0,
        current={"pe": 15.0, "ev_ebitda": 9.0, "fcf_yield": 0.05, "p_tbv": None},
        quality={"roic": 0.13, "fcf_margin": 0.18, "net_debt_ebitda": 1.1},
        history=hist, normalized=norm, dcf_cases=dcf,
        sources="edgar (primary), yfinance (non-primary)")
    comps = CompsResult(subject=subject, peers=[], peer_table=peer_table,
                        wacc=0.09, terminal_growth=0.025, forecast_years=10,
                        share_basis="wad")
    return ModelInputs(comps=comps, shares=38 * _M, net_debt=900 * _M,
                       tbv_per_share=float("nan"), is_bank=False, spinoff=None)


def main() -> None:
    out = project_root() / "models" / "templates" / "example_synthetic_model.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(synthetic_inputs(), out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

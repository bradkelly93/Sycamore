"""Phase 5 — Excel model-scaffold builder tests (OFFLINE).

These assert STRUCTURE: tabs, named ranges, the live formula STRINGS, source
tags, and the downside-first treatment — built from synthetic engine outputs, no
network. They deliberately do NOT assert formula EVALUATION: openpyxl stores
formula strings and never computes them, so "does the reverse-DCF tie out / does
the bear column flex / are there any #REF!/#DIV0!/#NAME?" is verified by a human
opening the file in Excel (see models/MODEL_NOTES.md). A green run here means the
scaffold is wired correctly, not that the model computes.
"""

from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import load_workbook

from sycamore_prep.comps.comps import CompsResult, TickerAnalysis
from sycamore_prep.comps.history import BandSummary, HistoryResult
from sycamore_prep.comps.normalized import NormalizedEarnings
from sycamore_prep.comps.reverse_dcf import DcfCase
from sycamore_prep.models import ModelInputs, build_workbook
from sycamore_prep.models.builder import (
    SHEET_BANK,
    SHEET_DCF,
    SHEET_FOOTBALL,
    SHEET_INPUTS,
    SHEET_NORM,
    SHEET_NOTES,
    SHEET_SOTP,
)
from sycamore_prep.spinoffs.discovery import SpinoffRecord

_M = 1_000_000


def _band(name: str, current, p25, median, p75, lower_is_cheap=True) -> BandSummary:
    return BandSummary(name, current, 6, 50.0, p25 * 0.9, p25, median, p75,
                       p75 * 1.1, lower_is_cheap)


def _dcf_case(label, wacc, tg, fcf0, implied_g, assumed_g, fv, price, mos) -> DcfCase:
    return DcfCase(label=label, wacc=wacc, terminal_growth=tg, fcf0=fcf0,
                   implied_growth=implied_g, implied_converged=True,
                   assumed_growth=assumed_g, fair_value_per_share=fv,
                   current_price=price, margin_of_safety=mos)


def _normalized() -> NormalizedEarnings:
    return NormalizedEarnings(
        window=5, n_used=5, mean_op_margin=0.16, trough_op_margin=0.10,
        current_revenue=1500 * _M, interest_expense=20 * _M,
        normalized_op_income=240 * _M, normalized_net_income=173.8 * _M,
        normalized_eps=6.95, trough_net_income=102.2 * _M, trough_eps=4.09,
        normalized_pe=8.63, trough_pe=14.67, trailing_pe=10.0,
        tax_rate=0.21, basis="synthetic")


def _peer_table(is_bank: bool) -> pd.DataFrame:
    rows = {
        "CW": (15.0, "CW"),
        "HEI": (30.0, "HEI"),
        "TDG": (26.0, "TDG"),
        "PEER_MEDIAN": (28.0, "PEER_MEDIAN"),
        "PEER_MEAN": (28.0, "PEER_MEAN"),
    }
    df = pd.DataFrame(
        {"pe": {k: v[0] for k, v in rows.items()},
         "name": {k: v[1] for k, v in rows.items()},
         "is_bank": {k: is_bank for k in rows}}
    )
    return df


def make_model_inputs(*, is_bank=False, with_spinoff=False) -> ModelInputs:
    """Synthetic ModelInputs — a fully populated non-bank (or bank) subject."""
    if is_bank:
        bands = {"PE": _band("PE", 11.0, 9.0, 11.0, 13.0),
                 "P_TBV": _band("P_TBV", 1.4, 1.1, 1.4, 1.7)}
        dcf = []
    else:
        bands = {"PE": _band("PE", 15.0, 12.0, 15.0, 18.0),
                 "EV_EBITDA": _band("EV_EBITDA", 9.0, 7.0, 9.0, 11.0),
                 "FCF_Yield": _band("FCF_Yield", 0.05, 0.03, 0.05, 0.07, lower_is_cheap=False)}
        dcf = [
            _dcf_case("bear", 0.10, 0.020, 240 * _M, 0.02, 0.04, 48.0, 60.0, -0.20),
            _dcf_case("base", 0.09, 0.025, 300 * _M, 0.05, 0.04, 70.0, 60.0, 0.1667),
            _dcf_case("bull", 0.08, 0.030, 320 * _M, 0.08, 0.04, 95.0, 60.0, 0.583),
        ]
    hist = HistoryResult(ticker="CW", share_basis="wad", is_bank=is_bank,
                         fy_end_prices=pd.Series(dtype=float),
                         market_caps=pd.Series(dtype=float), bands=bands)
    subject = TickerAnalysis(
        ticker="CW", name="Curtiss-Wright", is_bank=is_bank,
        market_cap=2300 * _M, price=60.0,
        current={"pe": 15.0, "ev_ebitda": None if is_bank else 9.0,
                 "fcf_yield": None if is_bank else 0.05,
                 "p_tbv": 1.4 if is_bank else None},
        quality={"roic": 0.13, "fcf_margin": 0.18,
                 "net_debt_ebitda": None if is_bank else 1.1},
        history=hist, normalized=_normalized(), dcf_cases=dcf,
        sources="edgar (primary), yfinance (non-primary)")
    comps = CompsResult(subject=subject, peers=[], peer_table=_peer_table(is_bank),
                        wacc=0.09, terminal_growth=0.025, forecast_years=10,
                        share_basis="wad")
    spin = None
    if with_spinoff:
        spin = SpinoffRecord(parent_ticker="CW", spinco_ticker="SPN",
                             spinco_name="SpinCo Inc", fcf=200 * _M,
                             net_debt_ebitda=1.5, roic=0.12, fcf_margin=0.16,
                             distribution_ratio="1:3", has_financials=True,
                             downside_flags=["forced_selling_window"])
    return ModelInputs(comps=comps, shares=38 * _M, net_debt=900 * _M,
                       tbv_per_share=32.0, is_bank=is_bank, spinoff=spin)


def _named_cell(wb, name):
    title, coord = next(wb.defined_names[name].destinations)
    return wb[title][coord]


def _all_formulas(ws) -> list[str]:
    out = []
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str):
                out.append(c.value)
    return out


# --------------------------------------------------------------------------- #
# Non-bank workbook
# --------------------------------------------------------------------------- #

@pytest.fixture
def nonbank_wb(tmp_path):
    out = tmp_path / "CW_model.xlsx"
    build_workbook(make_model_inputs(), out)
    return load_workbook(out)


def test_nonbank_tabs(nonbank_wb):
    assert set(nonbank_wb.sheetnames) == {
        SHEET_INPUTS, SHEET_DCF, SHEET_NORM, SHEET_FOOTBALL, SHEET_NOTES}
    assert SHEET_BANK not in nonbank_wb.sheetnames
    assert SHEET_SOTP not in nonbank_wb.sheetnames


def test_nonbank_named_ranges_exist(nonbank_wb):
    for name in ["WACC", "TERM_G", "YEARS", "TAX", "WACC_STRESS", "TG_STRESS",
                 "PRICE", "MKT_CAP", "SHARES", "NET_DEBT",
                 "FCFF0_BASE", "FCFF0_BEAR", "FCFF0_BULL", "ASSUMED_G",
                 "IMPLIED_G_BASE", "IMPLIED_G_BEAR", "IMPLIED_G_BULL",
                 "REVENUE", "MEAN_OPM", "TROUGH_OPM", "INT_EXP",
                 "PE_P25", "PE_MEDIAN", "PE_P75",
                 "PEER_PE_MIN", "PEER_PE_MEDIAN", "PEER_PE_MAX",
                 "FV_BEAR", "FV_BASE", "FV_BULL", "NORM_EPS", "TROUGH_EPS"]:
        assert name in nonbank_wb.defined_names, f"missing named range {name}"


def test_dcf_live_formulas_mirror_engine(nonbank_wb):
    f = _all_formulas(nonbank_wb[SHEET_DCF])
    blob = "\n".join(f)
    # Per-year PV off the growth/WACC drivers (forward DCF is fully live).
    assert any(s.startswith("=IF($A") and "ASSUMED_G" in s and "^" in s for s in f)
    # Gordon TV guarded against wacc<=tg; fair value = (EV-net debt)/shares; MoS vs price.
    assert "NA()" in blob
    assert any("/SHARES" in s for s in f)
    assert any("/PRICE-1" in s for s in f)
    # Reverse read: implied growth + tie-out vs current EV (mkt cap + net debt).
    assert any("IMPLIED_G_BASE" in s for s in f)
    assert any("MKT_CAP+NET_DEBT" in s for s in f)
    assert "Goal Seek" in blob


def test_normalized_formulas(nonbank_wb):
    f = _all_formulas(nonbank_wb[SHEET_NORM])
    assert "=MEAN_OPM*REVENUE" in f
    assert any(s == "=(MEAN_OPM*REVENUE-INT_EXP)*(1-TAX)" for s in f)
    assert any("PRICE/" in s for s in f)   # P/E


def test_football_references_dcf_and_bands(nonbank_wb):
    f = _all_formulas(nonbank_wb[SHEET_FOOTBALL])
    assert "=FV_BEAR" in f and "=FV_BASE" in f and "=FV_BULL" in f
    assert any("NORM_EPS*PE_MEDIAN" in s for s in f)
    assert any("PEER_PE" in s for s in f)


def test_inputs_source_tags(nonbank_wb):
    ws = nonbank_wb[SHEET_INPUTS]
    kinds, sources = set(), set()
    for row in ws.iter_rows(min_col=3, max_col=4):
        for c in row:
            if isinstance(c.value, str):
                kinds.add(c.value)
                sources.add(c.value)
    assert "edgar-primary" in kinds
    assert "yfinance-non-primary" in kinds
    assert "analyst-assumption" in kinds
    assert any("yfinance (non-primary)" in s for s in sources)
    assert any("EDGAR" in s for s in sources)


def test_downside_first_bear_styling_and_order(nonbank_wb):
    bear = _named_cell(nonbank_wb, "FV_BEAR")
    base = _named_cell(nonbank_wb, "FV_BASE")
    # Bear column is left of base (downside-first).
    assert bear.column < base.column
    # Bear fair-value cell carries the loud downside fill.
    assert "F8CBAD" in str(bear.fill.fgColor.rgb)


# --------------------------------------------------------------------------- #
# Bank workbook (P/TBV + normalized EPS; no FCFF DCF / SOTP)
# --------------------------------------------------------------------------- #

@pytest.fixture
def bank_wb(tmp_path):
    out = tmp_path / "UMBF_model.xlsx"
    build_workbook(make_model_inputs(is_bank=True), out)
    return load_workbook(out)


def test_bank_tabs_swap_dcf_for_bank_valuation(bank_wb):
    assert SHEET_BANK in bank_wb.sheetnames
    assert SHEET_DCF not in bank_wb.sheetnames
    assert SHEET_SOTP not in bank_wb.sheetnames


def test_bank_named_ranges_and_no_dcf_names(bank_wb):
    for name in ["TBV_PS", "PTBV_TARGET", "NORM_PE_TARGET",
                 "PTBV_P25", "PTBV_MEDIAN", "PTBV_P75"]:
        assert name in bank_wb.defined_names
    for absent in ["FCFF0_BASE", "IMPLIED_G_BASE", "FV_BASE"]:
        assert absent not in bank_wb.defined_names


def test_bank_valuation_formulas(bank_wb):
    f = _all_formulas(bank_wb[SHEET_BANK])
    assert "=PTBV_TARGET*TBV_PS" in f
    assert any("NORM_PE_TARGET*NORM_EPS" in s for s in f)
    foot = _all_formulas(bank_wb[SHEET_FOOTBALL])
    assert any("PTBV_MEDIAN*TBV_PS" in s for s in foot)


# --------------------------------------------------------------------------- #
# SOTP (only when a spin-off linkage is provided)
# --------------------------------------------------------------------------- #

def test_sotp_tab_only_with_spinoff(tmp_path):
    out = tmp_path / "CW_model.xlsx"
    build_workbook(make_model_inputs(with_spinoff=True), out)
    wb = load_workbook(out)
    assert SHEET_SOTP in wb.sheetnames
    f = _all_formulas(wb[SHEET_SOTP])
    blob = "\n".join(f)
    assert any("/SHARES" in s for s in f)        # implied value per share
    assert any("/PRICE-1" in s for s in f)        # downside-first read vs price
    assert "forced_selling_window" in blob        # tracker downside flag surfaced


def _notes_text(wb) -> str:
    return "\n".join(
        c.value for row in wb[SHEET_NOTES].iter_rows() for c in row
        if isinstance(c.value, str)
    )


def test_spinoff_notes_warns_about_assumed_growth(tmp_path):
    out = tmp_path / "CW_model.xlsx"
    build_workbook(make_model_inputs(with_spinoff=True), out)
    txt = _notes_text(load_workbook(out))
    assert "RECENT SPIN-OFF" in txt
    assert "ASSUMED_G" in txt


def test_no_spinoff_no_warning(tmp_path):
    out = tmp_path / "CW_model.xlsx"
    build_workbook(make_model_inputs(), out)
    assert "RECENT SPIN-OFF" not in _notes_text(load_workbook(out))


# --------------------------------------------------------------------------- #
# Thin end-to-end via the comps monkeypatch fixture (offline)
# --------------------------------------------------------------------------- #

from tests.test_comps import patched  # noqa: E402,F401  (reuse the offline fixture)

from sycamore_prep.models import build_models  # noqa: E402


def test_build_models_end_to_end_offline(patched):
    out = patched / "AAA_model.xlsx"
    res = build_models("AAA", peers=["BBB"], output_path=out)
    assert out.exists()
    assert not res.is_bank
    wb = load_workbook(out)
    assert SHEET_DCF in wb.sheetnames
    assert "WACC" in wb.defined_names and "FV_BASE" in wb.defined_names


# --------------------------------------------------------------------------- #
# The Excel formula arithmetic MUST mirror the engine (so the file ties to the
# `comps` CLI). openpyxl won't evaluate, so we replicate the exact formula math
# in Python and assert it equals the engine's own DCF — same identity Excel will
# compute on open. Guards the closed-form used by the reverse-DCF tie-out cell.
# --------------------------------------------------------------------------- #
import math  # noqa: E402

from sycamore_prep.comps.reverse_dcf import (  # noqa: E402
    dcf_enterprise_value,
    dcf_equity_value_per_share,
    solve_implied_growth,
)


def _ev_closed_form(fcf0, g, wacc, tg, years):
    """The arithmetic the DCF/reverse cells encode: growing-annuity explicit PV
    + discounted Gordon terminal. Must equal the engine's loop."""
    x = (1 + g) / (1 + wacc)
    explicit = fcf0 * x * (1 - x ** years) / (1 - x)
    tv = fcf0 * (1 + g) ** years * (1 + tg) / (wacc - tg) / (1 + wacc) ** years
    return explicit + tv


def test_excel_formula_math_matches_engine():
    fcf0, wacc, tg, years = 300e6, 0.09, 0.025, 10
    net_debt, shares = 900e6, 38e6
    assumed = 0.04
    # Forward: the closed form the strip+TV sum to == the engine's enterprise value.
    ev_engine = dcf_enterprise_value(fcf0, assumed, wacc, tg, years)
    assert _ev_closed_form(fcf0, assumed, wacc, tg, years) == pytest.approx(ev_engine, rel=1e-9)
    # Fair value/share identity (what the Excel "(EV-net debt)/shares" computes).
    fv = (ev_engine - net_debt) / shares
    assert fv == pytest.approx(
        dcf_equity_value_per_share(fcf0, assumed, wacc, tg, years, net_debt, shares), rel=1e-12)
    # Reverse tie-out: at the solver's implied growth, EV@g == current EV (residual≈0),
    # which is exactly what the seeded CHECK cell shows on open.
    mcap = 2300e6
    current_ev = mcap + net_debt
    g_imp, converged = solve_implied_growth(current_ev, fcf0, wacc, tg, years)
    assert converged
    residual = _ev_closed_form(fcf0, g_imp, wacc, tg, years) - current_ev
    assert abs(residual) < 1e-4 * current_ev


# --------------------------------------------------------------------------- #
# Static formula lint: balanced parens + every name token is a defined range or
# a known Excel function (catches #NAME?/syntax errors before Excel does).
# --------------------------------------------------------------------------- #
import re  # noqa: E402

_FUNCS = {"IF", "IFERROR", "NA", "SUM", "MAX", "MIN", "ABS", "TEXT"}
_QUOTED_RE = re.compile(r'"[^"]*"')
# Maximal tokens that start with a letter/_/$ — so FCFF0_BASE and B$7 stay whole.
_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.]*")
_CELL_RE = re.compile(r"^\$?[A-Z]{1,3}\$?[0-9]+$")   # A1, B$7, $A12, AA10


def _lint_sheet(ws, defined: set[str]) -> list[str]:
    problems = []
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if not (isinstance(v, str) and v.startswith("=")):
                continue
            body = _QUOTED_RE.sub("", v[1:])           # drop string literals
            if body.count("(") != body.count(")"):
                problems.append(f"{ws.title}!{cell.coordinate}: unbalanced parens: {v}")
            for tok in _TOKEN_RE.findall(body):
                if _CELL_RE.match(tok) or tok in _FUNCS or tok in defined:
                    continue
                problems.append(f"{ws.title}!{cell.coordinate}: unknown token {tok!r} in {v}")
    return problems


@pytest.mark.parametrize("kwargs", [
    {}, {"is_bank": True}, {"with_spinoff": True},
])
def test_all_formulas_lint_clean(tmp_path, kwargs):
    out = tmp_path / "m.xlsx"
    build_workbook(make_model_inputs(**kwargs), out)
    wb = load_workbook(out)
    defined = set(wb.defined_names)
    problems = []
    for ws in wb.worksheets:
        problems += _lint_sheet(ws, defined)
    assert not problems, "\n".join(problems)

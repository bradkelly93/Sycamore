"""openpyxl styling + helpers for the model scaffolds.

Kept separate from ``builder.py`` so the workbook assembly stays readable, the
same way ``comps/report.py`` owns the comps formatting. The cell-type palette is
the audit trail: every value cell is visually tagged input / formula / EDGAR-
primary / yfinance-non-primary / analyst-assumption / downside, so an analyst
opening the workbook can see at a glance what is verified vs. a judgment call
(CLAUDE.md #3, #4). Downside cells get the loudest treatment (CLAUDE.md #1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import absolute_coordinate, get_column_letter, quote_sheetname

# --------------------------------------------------------------------------- #
# Cell-type vocabulary (the source-tag legend lives on the Notes tab)
# --------------------------------------------------------------------------- #
INPUT = "input"                       # analyst-editable raw input
FORMULA = "formula"                   # computed in-sheet — do not overtype
EDGAR = "edgar-primary"               # verified primary source (SEC XBRL)
YFINANCE = "yfinance-non-primary"     # convenience data, unverified
ASSUMPTION = "analyst-assumption"     # judgment call
DERIVED = "derived"                   # derived statistic (own history / peers)
SEED = "formula-seed"                 # seeded engine value an Excel CHECK re-derives
BEAR = "bear"                         # downside anchor — surfaced most prominently

# fgColor + font per kind. Pale fills so the sheet stays readable; bear is the
# one deliberately loud (deep orange + bold red) so downside never hides.
_PALETTE: dict[str, tuple[str, dict]] = {
    INPUT:      ("FFF2CC", {}),
    FORMULA:    ("E2EFDA", {}),
    EDGAR:      ("D9E1F2", {}),
    YFINANCE:   ("FCE4D6", {"italic": True}),
    ASSUMPTION: ("FFF2CC", {"italic": True}),
    DERIVED:    ("EDEDED", {"italic": True}),
    SEED:       ("FFF2CC", {"italic": True}),
    BEAR:       ("F8CBAD", {"bold": True, "color": "C00000"}),
}

# Number formats
FMT_PCT = "0.0%"
FMT_PCT2 = "0.00%"
FMT_MULT = '0.00"x"'
FMT_PRICE = "#,##0.00"
FMT_USD_M = '#,##0,,"M"'      # value in $ shown in millions
FMT_INT = "#,##0"

HEADER_FILL = PatternFill("solid", fgColor="DDDDDD")
HEADER_FONT = Font(bold=True)
TITLE_FONT = Font(bold=True, size=13)


def safe(v):
    """Coerce a value to something openpyxl can store; NaN/Inf -> None (blank).

    Copied from ``comps/report.py`` so the scaffolds and the comps tear-sheet
    handle missing data identically (blank, never a silent zero)."""
    if isinstance(v, np.generic):
        v = v.item()
    if v is None:
        return None
    if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def style_cell(cell, kind: str | None = None, number_format: str | None = None) -> None:
    """Apply the kind fill/font + an optional number format to a single cell."""
    if kind and kind in _PALETTE:
        fg, font_kw = _PALETTE[kind]
        cell.fill = PatternFill("solid", fgColor=fg)
        if font_kw:
            cell.font = Font(**font_kw)
    if number_format:
        cell.number_format = number_format


def write_cell(ws, row: int, col: int, value, kind: str | None = None,
               number_format: str | None = None):
    """Write a (coerced) value to a cell and style it. Returns the cell."""
    cell = ws.cell(row=row, column=col, value=safe(value) if not _is_formula(value) else value)
    style_cell(cell, kind, number_format)
    return cell


def _is_formula(v) -> bool:
    return isinstance(v, str) and v.startswith("=")


def header_row(ws, row: int, labels: list[str], start_col: int = 1) -> None:
    """Bold gray header (mirrors comps/report.py house style)."""
    for i, lab in enumerate(labels):
        c = ws.cell(row=row, column=start_col + i, value=lab)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="left")


def title(ws, text: str, row: int = 1) -> None:
    c = ws.cell(row=row, column=1, value=text)
    c.font = TITLE_FONT


def set_widths(ws, widths: dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def ref(sheet_title: str, coord: str) -> str:
    """Absolute, sheet-quoted reference string for a defined name, e.g.
    ``ref("Inputs & Sources", "B5") -> "'Inputs & Sources'!$B$5"``. Uses
    openpyxl's own quoting so sheet names with spaces/`&` are handled."""
    return f"{quote_sheetname(sheet_title)}!{absolute_coordinate(coord)}"

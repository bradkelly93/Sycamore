"""Serialization boundary — the ONLY module (with ``artifacts``) allowed to
touch pandas / numpy / Path on the way to a view-model.

Every value that enters a :class:`~sycamore_prep.service.viewmodels.Figure` (or a
record dict) passes through :func:`clean_scalar`, so the JSON the FastAPI side
emits is strict (no ``NaN`` / ``Infinity`` tokens, no numpy scalars, no ``NaT``).
Keep this module free of engine imports — it operates on frames / scalars only.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def clean_scalar(v: Any) -> Any:
    """Coerce one value to a strict-JSON-safe Python scalar.

    - ``None`` / ``NaN`` / ``NaT`` / ``±inf`` -> ``None`` (``json``/pydantic would
      otherwise emit the invalid ``NaN``/``Infinity`` tokens that break strict
      parsers and the HTMX ``fetch`` path).
    - numpy scalars -> their Python equivalents (``np.float64`` -> ``float`` …).
    - everything else (str, bool, list, dict, Python number) is returned as-is.
    """
    if v is None:
        return None
    # pandas NA / NaT / np.nan on a scalar. Guarded: pd.isna on an array raises.
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    return v


def df_to_records(
    df: pd.DataFrame | None,
    column_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Tidy DataFrame -> ``list[dict]`` of JSON-safe records.

    A named (non-range) index is surfaced as a leading column (the screener is
    indexed by ``ticker``). ``column_order`` puts the requested columns first
    (downside-first ordering lives in the caller); any remaining columns follow
    in their existing order so nothing is silently dropped.
    """
    if df is None or len(df) == 0:
        return []
    d = df
    if d.index.name is not None:
        d = d.reset_index()
    ordered = [c for c in (column_order or []) if c in d.columns]
    ordered += [c for c in d.columns if c not in ordered]
    return [{c: clean_scalar(row[c]) for c in ordered} for _, row in d.iterrows()]


def split_flags(v: Any) -> list[str]:
    """Normalize a flags cell (already comma-joined string, a list, or NaN) into
    a clean ``list[str]`` — the form the UI renders as risk chips."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    try:
        if pd.isna(v):
            return []
    except (TypeError, ValueError):
        pass
    return [tok.strip() for tok in str(v).split(",") if tok.strip()]


def split_sources(v: Any) -> list[str]:
    """Split a source-tag string into individual tags, respecting parentheses so a
    tag like ``trend-proxy (non-primary, technical)`` stays intact (the engines join
    tags with ``", "`` but a tag can itself contain a comma inside its parens)."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    try:
        if pd.isna(v):
            return []
    except (TypeError, ValueError):
        pass
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in str(v):
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            tok = "".join(cur).strip()
            if tok:
                out.append(tok)
            cur = []
        else:
            cur.append(ch)
    tok = "".join(cur).strip()
    if tok:
        out.append(tok)
    return out

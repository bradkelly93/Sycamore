"""Pure display formatting (rendering, not analytics) shared by the Jinja templates.

Turns a view-model ``Figure``'s ``value`` + ``fmt`` into a human string. The §3
metadata (source/primary/flag) is already on the Figure; this only formats numbers.
"""

from __future__ import annotations


def fmt_value(value, fmt: str = "raw") -> str:
    if value is None:
        return "—"
    try:
        if fmt == "pct":
            return f"{float(value) * 100:.1f}%"
        if fmt == "mult":
            return f"{float(value):.1f}×"
        if fmt == "ccy":
            return _ccy(float(value))
        if fmt == "int":
            return f"{int(value):,}"
        if isinstance(value, float):
            return f"{value:.2f}"
    except (TypeError, ValueError):
        return str(value)
    return str(value)


def _ccy(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.2f}"

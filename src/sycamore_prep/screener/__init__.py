"""Three-attribute quality-value screener (Sycamore frame).

Per CLAUDE.md: every score decomposes into Quality / Valuation / Improving
Fundamentals — these three sub-scores are reported separately and never
collapsed into one opaque number. Composite rank is the sort key only.
"""

from .screen import run_screener, ScreenerRow

__all__ = ["run_screener", "ScreenerRow"]

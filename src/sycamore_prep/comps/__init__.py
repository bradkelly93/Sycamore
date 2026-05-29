"""comps module — peer comps, normalized earnings, and reverse DCF (Phase 3).

See CLAUDE.md: the three valuation lenses (discount-to-own-history, peer-relative,
reverse-DCF implied growth) stay decomposed and downside is surfaced first.
"""

from .comps import CompsResult, TickerAnalysis, run_comps

__all__ = ["run_comps", "CompsResult", "TickerAnalysis"]

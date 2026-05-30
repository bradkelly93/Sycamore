"""Prediction-market overlay — a NON-PRIMARY, read-only signal layer.

A separate decision-support lens (CLAUDE.md): it reads how prediction markets
(Polymarket) are positioned across four apertures around a name —

    company  → markets naming the stock
    peer     → markets naming a configured peer (read-through risk/opportunity)
    industry → sector-keyword markets (oil for E&P, FDA for pharma, …)
    macro    → broad markets (rates, recession, commodities) that bear on it

— to surface opportunity or risk the bottom-up tools can't see. It is WALLED
OFF from every scoring/valuation path: nothing here ever feeds the screener
composite, comps, or fair value. Downside read-throughs are sorted to the top
(downside-first). Every row is tagged `source="polymarket (non-primary)"`.
"""

from .mapping import (
    MAPPING_COLUMNS,
    empty_mapping,
    load_mapping,
    save_mapping,
    upsert,
)
from .discover import (
    relevance_score,
    guess_event_type_direction,
    discover_for_ticker,
)
from .overlay import (
    OVERLAY_COLUMNS,
    build_overlay,
    screener_annotations,
    write_overlay,
)

__all__ = [
    "MAPPING_COLUMNS", "empty_mapping", "load_mapping", "save_mapping", "upsert",
    "relevance_score", "guess_event_type_direction", "discover_for_ticker",
    "OVERLAY_COLUMNS", "build_overlay", "screener_annotations", "write_overlay",
]

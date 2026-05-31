"""UI-agnostic service layer — the thin seam both front-ends render.

Wraps the engine entry points (``screener.run_screener``, ``comps.run_comps``,
``pipeline``, ``universe``, ``prediction_markets.mapping`` …) into plain, JSON-safe
pydantic view-models that already carry the CLAUDE.md / UI_SPEC §3 ethos: a source
tag on every figure, the three attributes kept separate, downside fields flagged,
and the NON-PRIMARY overlays segregated + walled off from the rank. No UI imports
live here; this is the substrate the Streamlit and FastAPI Explorers share.
"""

from __future__ import annotations

from . import (
    artifacts,
    creds,
    jobs,
    overlays,
    pipeline,
    prediction,
    screener,
    serde,
    spinoffs,
    universe,
    workup,
)
from .viewmodels import (
    ArtifactRef,
    BandView,
    CredsStatus,
    DcfCaseView,
    DossierView,
    Figure,
    JobView,
    MappingRowView,
    MappingView,
    NameWorkupView,
    NormalizedView,
    OverlayView,
    PipelineRunRef,
    PipelineView,
    RankProof,
    RankUnchangedClaim,
    ScreenerRowView,
    ScreenerView,
    SpinoffsView,
    SycamoreHoldingRow,
    UniverseView,
    VolPanelView,
    fig,
)

__all__ = [
    # submodules (facades)
    "artifacts", "creds", "jobs", "overlays", "pipeline", "prediction",
    "screener", "serde", "spinoffs", "universe", "workup",
    # view-models
    "ArtifactRef", "BandView", "CredsStatus", "DcfCaseView", "DossierView",
    "Figure", "JobView", "MappingRowView", "MappingView", "NameWorkupView",
    "NormalizedView", "OverlayView", "PipelineRunRef", "PipelineView", "RankProof",
    "RankUnchangedClaim", "ScreenerRowView", "ScreenerView", "SpinoffsView",
    "SycamoreHoldingRow", "UniverseView", "VolPanelView", "fig",
]

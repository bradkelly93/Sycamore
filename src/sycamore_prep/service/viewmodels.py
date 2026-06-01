"""UI-agnostic view-models (pydantic v2) — the substrate both front-ends render.

Every model here is JSON-safe (no DataFrame / Path / numpy). The §3 ethos is
carried *structurally*:

- **Source tag on every number** — the atomic renderable is :class:`Figure`
  (``value`` + ``source`` + ``primary``). There is no bare ``float`` in a view.
- **Downside-first** — ``Figure.flag`` ("risk"/"warn") drives red/amber; the
  service sets it on margin-of-safety, bear case, breaches, negative-space rows.
- **Three attributes never collapsed** — ``q1_quality`` / ``q2_valuation`` /
  ``q3_improving`` are three distinct fields; ``composite_rank`` is a sort key
  only (``role="sort_key_only"``).
- **Overlays segregated + NON-PRIMARY** — overlay panels carry ``non_primary``
  and ``affects_rank=False``, mirroring the code invariant (screen.py:463/488).
- **Excel = link/download only** — workbooks are :class:`ArtifactRef`, never a
  live formula reimplementation and never an absolute path.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .serde import clean_scalar

# Canonical source tags (match the engines' own strings).
EDGAR = "edgar (primary)"
YFINANCE = "yfinance (non-primary)"
TASTYTRADE = "tastytrade (non-primary)"
TRADINGVIEW = "tradingview (non-primary, technical)"
TREND = "trend-proxy (non-primary, technical)"
POLYMARKET = "polymarket (non-primary)"


def is_primary(source: str | None) -> bool:
    """Primary == SEC EDGAR fundamentals. Everything else (yfinance prices,
    tastytrade vol, tradingview, polymarket) is non-primary. Honors the engines'
    explicit ``(primary)`` / ``(non-primary)`` tags when present."""
    s = (source or "").lower()
    if "non-primary" in s:
        return False
    if "(primary)" in s:
        return True
    return s.strip().startswith("edgar")


# --------------------------------------------------------------------------- #
# Atomic primitives
# --------------------------------------------------------------------------- #
class Figure(BaseModel):
    """One rendered number + its provenance. The §3.1 / §3.2 enforcement point."""

    value: float | int | str | None = None
    source: str = ""
    primary: bool = False
    fmt: str = "raw"          # "pct" | "mult" | "ccy" | "int" | "raw"
    flag: str | None = None   # "risk" | "warn" | None


def fig(value, source: str = "", *, fmt: str = "raw", flag: str | None = None,
        primary: bool | None = None) -> Figure:
    """Build a JSON-safe :class:`Figure`, cleaning ``value`` at the boundary."""
    return Figure(
        value=clean_scalar(value),
        source=source,
        primary=is_primary(source) if primary is None else primary,
        fmt=fmt,
        flag=flag,
    )


class ArtifactRef(BaseModel):
    """A downloadable engine artifact (xlsx/md/csv/folder). ``token`` resolves via
    ``service.artifacts.resolve`` — never an absolute path (path-traversal safe)."""

    kind: str
    filename: str
    token: str


# --------------------------------------------------------------------------- #
# Overlays (NON-PRIMARY, walled off from scoring)
# --------------------------------------------------------------------------- #
class OverlayView(BaseModel):
    """Container the UI renders in the walled-off panel. ``affects_rank`` is a
    constant False — it mirrors the code invariant, it is not a runtime read."""

    kind: str                       # "vol" | "tv" | "prediction"
    label: str = "NON-PRIMARY"
    non_primary: bool = True
    affects_rank: bool = False
    source: str = ""
    columns: list[str] = Field(default_factory=list)
    note: str | None = None


class VolPanelView(BaseModel):
    non_primary: bool = True
    source: str = TASTYTRADE
    iv_rank: Figure | None = None
    iv_percentile: Figure | None = None
    iv_index: Figure | None = None
    expected_move_30d_pct: Figure | None = None
    expected_move_earnings_pct: Figure | None = None
    days_to_earnings: Figure | None = None
    next_earnings_date: str | None = None
    vol_beta: Figure | None = None
    liquidity_rating: Figure | None = None
    sigma_down_30d_price: Figure | None = None
    mos_cross_check: dict | None = None   # {sigma_down_price, mos_floor, breaches, cushion_pct}
    vol_flags: list[str] = Field(default_factory=list)
    note: str | None = None


# --------------------------------------------------------------------------- #
# Screener
# --------------------------------------------------------------------------- #
class RankUnchangedClaim(BaseModel):
    """The §3.4 affordance. ``guaranteed`` is the cheap structural claim (with a
    code citation); ``verified`` is filled only by an on-demand dual run."""

    guaranteed: bool = True
    citation: str = "screener/screen.py:463 (score reads fundamentals only) + :488-520 (overlays spliced after rank)"
    verify_available: bool = True
    verified: bool | None = None


class ScreenerRowView(BaseModel):
    ticker: str
    name: str | None = None
    gics_sector: str | None = None
    is_bank: bool = False
    market_cap: Figure | None = None
    # The three attributes — kept separate, never summed.
    q1_quality: Figure | None = None
    q2_valuation: Figure | None = None
    q3_improving: Figure | None = None
    composite_score: Figure | None = None
    composite_rank: int | None = None
    rank_role: str = "sort_key_only"
    negative_space: bool = False
    ns_flags: list[str] = Field(default_factory=list)
    flag: str | None = None             # row-level risk (negative_space)
    raw_components: dict[str, Figure] = Field(default_factory=dict)
    # NON-PRIMARY overlay cells (right panel only).
    vol: dict[str, Figure] | None = None
    tv: dict[str, object] | None = None
    prediction: dict[str, object] | None = None
    sources: list[str] = Field(default_factory=list)
    error: str | None = None


class ScreenerView(BaseModel):
    tickers_requested: list[str] | None = None
    sector: str | None = None
    limit: int | None = None
    rows: list[ScreenerRowView] = Field(default_factory=list)
    column_order: list[str] = Field(default_factory=list)
    overlay_columns: dict[str, list[str]] = Field(default_factory=dict)
    overlays_active: dict[str, bool] = Field(default_factory=dict)
    rank_unchanged: RankUnchangedClaim = Field(default_factory=RankUnchangedClaim)
    notes: dict[str, str | None] = Field(default_factory=dict)
    source_summary: list[str] = Field(default_factory=list)
    screener_artifact: ArtifactRef | None = None   # full ranked xlsx (auditable download)


class RankProof(BaseModel):
    """On-demand dual-run proof that overlays never move the rank."""

    identical: bool
    rows: list[dict] = Field(default_factory=list)   # {ticker, rank_overlays_off, rank_overlays_on}
    overlays_checked: list[str] = Field(default_factory=list)
    note: str | None = None


# --------------------------------------------------------------------------- #
# Name workup (comps + reverse DCF + normalized + bands + vol)
# --------------------------------------------------------------------------- #
class DcfCaseView(BaseModel):
    label: str                          # "bear" | "base" | "bull"
    wacc: Figure | None = None
    terminal_growth: Figure | None = None
    fcf0: Figure | None = None
    implied_growth: Figure | None = None
    implied_converged: bool = False
    assumed_growth: Figure | None = None
    fair_value_per_share: Figure | None = None
    current_price: Figure | None = None
    margin_of_safety: Figure | None = None


class BandView(BaseModel):
    multiple: str
    current: Figure | None = None
    n: int = 0
    percentile_cheap: Figure | None = None
    lower_is_cheap: bool = True
    p_min: Figure | None = None
    p25: Figure | None = None
    median: Figure | None = None
    p75: Figure | None = None
    p_max: Figure | None = None
    series_artifact: ArtifactRef | None = None   # full FY series via download, not inline


class NormalizedView(BaseModel):
    window: int = 0
    n_used: int = 0
    normalized_eps: Figure | None = None
    normalized_pe: Figure | None = None
    trough_eps: Figure | None = None       # downside anchor
    trough_pe: Figure | None = None        # downside anchor
    trailing_pe: Figure | None = None
    basis: str | None = None


class NameWorkupView(BaseModel):
    ticker: str
    name: str | None = None
    is_bank: bool = False
    market_cap: Figure | None = None
    price: Figure | None = None
    current: dict[str, Figure] = Field(default_factory=dict)
    quality: dict[str, Figure] = Field(default_factory=dict)
    # Headline downside numbers (rendered at top).
    base_margin_of_safety: Figure | None = None
    base_implied_growth: Figure | None = None
    dcf_cases: list[DcfCaseView] = Field(default_factory=list)   # ordered bear, base, bull
    normalized: NormalizedView | None = None
    bands: list[BandView] = Field(default_factory=list)
    vol_panel: VolPanelView | None = None
    model_artifact: ArtifactRef | None = None    # link/download only; build is Phase B
    comps_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    peers: list[str] = Field(default_factory=list)
    peers_source: str = ""
    wacc: Figure | None = None
    terminal_growth: Figure | None = None
    sources: list[str] = Field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
class PipelineRunRef(BaseModel):
    run_token: str
    name: str                # the pipeline_<ts> folder name
    generated: str | None = None
    shortlist: list[str] = Field(default_factory=list)


class DossierView(BaseModel):
    ticker: str
    name: str | None = None
    gics_sector: str | None = None
    market_cap: Figure | None = None
    q1_quality: Figure | None = None
    q2_valuation: Figure | None = None
    q3_improving: Figure | None = None
    composite_score: Figure | None = None
    composite_rank: int | None = None
    # Downside group (rendered before any upside).
    margin_of_safety_base: Figure | None = None
    implied_growth_base: Figure | None = None
    trough_pe: Figure | None = None
    ns_flags: list[str] = Field(default_factory=list)
    is_bank: bool = False
    recent_spinoff: bool = False
    peers_used: list[str] = Field(default_factory=list)
    peers_source: str = ""
    dossier_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    error: str | None = None


class PipelineView(BaseModel):
    run_token: str
    name: str
    generated: str | None = None
    params: dict = Field(default_factory=dict)
    sources: str | None = None
    shortlist: list[str] = Field(default_factory=list)
    dossiers: list[DossierView] = Field(default_factory=list)
    index_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    manifest_artifact: ArtifactRef | None = None
    manifest: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Prediction-market mapping (display in A; write-back in B)
# --------------------------------------------------------------------------- #
class MappingRowView(BaseModel):
    ticker: str
    aperture: str | None = None
    slug: str | None = None
    question: str | None = None
    event_type: str | None = None
    direction: str | None = None
    relevance_score: Figure | None = None
    confirmed: bool = False
    notes: str = ""
    human_owned: bool = False        # confirmed or noted -> protected from re-discovery


class MappingView(BaseModel):
    rows: list[MappingRowView] = Field(default_factory=list)
    path_note: str = ""
    editable: bool = False           # False in Phase A; True in Phase B
    counts: dict[str, int] = Field(default_factory=dict)
    note: str | None = None


# --------------------------------------------------------------------------- #
# Universe / creds / jobs
# --------------------------------------------------------------------------- #
class SycamoreHoldingRow(BaseModel):
    fund: str | None = None
    ticker: str
    name: str | None = None
    gics_sector: str | None = None
    weight_pct: Figure | None = None
    position_value: Figure | None = None


class UniverseView(BaseModel):
    built: bool = False
    count: int = 0
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    sector_breakdown: dict[str, int] = Field(default_factory=dict)
    sycamore_owned: int = 0
    instructions: str | None = None
    csv_artifact: ArtifactRef | None = None
    # Sycamore fund holdings (the overlay) — available even before a build.
    sycamore_funds: dict[str, int] = Field(default_factory=dict)
    sycamore_sectors: dict[str, int] = Field(default_factory=dict)
    sycamore_holdings: list[SycamoreHoldingRow] = Field(default_factory=list)
    sycamore_note: str | None = None


class SpinoffsView(BaseModel):
    """Read-only in Phase A: cached scan/track artifacts as downloads. Live
    scan/track (a network action) is wired in Phase B."""

    artifacts: list[ArtifactRef] = Field(default_factory=list)
    note: str | None = None


class CredsStatus(BaseModel):
    sec_user_agent: dict = Field(default_factory=dict)      # {detected, origin}
    tastytrade: dict = Field(default_factory=dict)          # {detected, missing}
    tradingview: dict = Field(default_factory=dict)         # {sessionid_detected, enabled, mode}
    prediction: dict = Field(default_factory=dict)          # {enabled}
    valuation_defaults: dict = Field(default_factory=dict)  # {wacc, terminal_growth, forecast_years}
    peers: dict[str, list[str]] = Field(default_factory=dict)


class JobView(BaseModel):
    id: str
    kind: str
    state: str                       # "queued" | "running" | "done" | "error"
    progress: float | None = None
    note: str | None = None
    started: str | None = None
    finished: str | None = None
    elapsed_s: float | None = None
    result_ref: ArtifactRef | None = None
    result_summary: dict = Field(default_factory=dict)   # small key->value summary
    result_url: str | None = None                        # where to send the user on done
    error: str | None = None


class ActionResult(BaseModel):
    """Outcome of a fast, inline single-name action (Phase B)."""

    ok: bool
    title: str
    message: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    detail: dict[str, Figure] = Field(default_factory=dict)
    link: str | None = None          # e.g. /workup/<ticker> to see the result

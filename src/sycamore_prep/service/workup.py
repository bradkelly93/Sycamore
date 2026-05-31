"""Service wrapper for the comps + reverse-DCF + normalized-earnings + own-history
workup of a single name.

Wraps ``comps.run_comps`` (no recompute) into a :class:`NameWorkupView`: the
headline downside numbers (base MoS / implied growth) up top, the reverse-DCF
cases ordered bear→base→bull, own-history bands, normalized/trough earnings, and
an optional NON-PRIMARY vol panel. The Excel *model* is link/download only
(``model_artifact``) — building it is a Phase B action.
"""

from __future__ import annotations

from ..adapters import TastytradeProvider
from ..comps import run_comps
from ..config import models_dir
from ..metrics.volatility import volatility_overlay
from . import artifacts
from .overlays import vol_panel_from_overlay
from .serde import clean_scalar, split_sources
from .viewmodels import (
    EDGAR,
    YFINANCE,
    BandView,
    DcfCaseView,
    NameWorkupView,
    NormalizedView,
    fig,
)

MIXED = "edgar + yfinance price (non-primary)"
_CASE_ORDER = {"bear": 0, "base": 1, "bull": 2}


def _dcf_case_view(c) -> DcfCaseView:
    mos = c.margin_of_safety
    return DcfCaseView(
        label=c.label,
        wacc=fig(c.wacc, MIXED, fmt="pct"),
        terminal_growth=fig(c.terminal_growth, MIXED, fmt="pct"),
        fcf0=fig(c.fcf0, EDGAR, fmt="ccy"),
        implied_growth=fig(c.implied_growth, MIXED, fmt="pct"),
        implied_converged=bool(c.implied_converged),
        assumed_growth=fig(c.assumed_growth, MIXED, fmt="pct"),
        fair_value_per_share=fig(c.fair_value_per_share, MIXED, fmt="ccy"),
        current_price=fig(c.current_price, YFINANCE, fmt="ccy"),
        margin_of_safety=fig(mos, MIXED, fmt="pct",
                             flag="risk" if (mos is not None and mos == mos and mos < 0) else None),
    )


def _band_view(b) -> BandView:
    pct_band = "yield" in b.multiple.lower()
    f = "pct" if pct_band else "mult"
    return BandView(
        multiple=b.multiple,
        current=fig(b.current, MIXED, fmt=f),
        n=int(b.n),
        percentile_cheap=fig(b.percentile_cheap, MIXED),
        lower_is_cheap=bool(b.lower_is_cheap),
        p_min=fig(b.p_min, MIXED, fmt=f),
        p25=fig(b.p25, MIXED, fmt=f),
        median=fig(b.median, MIXED, fmt=f),
        p75=fig(b.p75, MIXED, fmt=f),
        p_max=fig(b.p_max, MIXED, fmt=f),
    )


def _normalized_view(n) -> NormalizedView | None:
    if n is None:
        return None
    return NormalizedView(
        window=int(n.window),
        n_used=int(n.n_used),
        normalized_eps=fig(n.normalized_eps, EDGAR, fmt="ccy"),
        normalized_pe=fig(n.normalized_pe, MIXED, fmt="mult"),
        trough_eps=fig(n.trough_eps, EDGAR, fmt="ccy", flag="warn"),
        trough_pe=fig(n.trough_pe, MIXED, fmt="mult", flag="warn"),
        trailing_pe=fig(n.trailing_pe, MIXED, fmt="mult"),
        basis=clean_scalar(n.basis),
    )


def _quality_view(q: dict) -> dict:
    out = {}
    if "roic" in q:
        out["roic"] = fig(q.get("roic"), EDGAR, fmt="pct")
    if "fcf_margin" in q:
        out["fcf_margin"] = fig(q.get("fcf_margin"), EDGAR, fmt="pct")
    if "net_debt_ebitda" in q:
        nd = q.get("net_debt_ebitda")
        out["net_debt_ebitda"] = fig(nd, EDGAR, fmt="mult",
                                     flag="warn" if (nd is not None and nd == nd and nd > 3) else None)
    return out


def _current_view(cur: dict) -> dict:
    fmts = {"pe": "mult", "ev_ebitda": "mult", "p_tbv": "mult", "fcf_yield": "pct"}
    return {k: fig(cur.get(k), MIXED, fmt=fmts.get(k, "raw")) for k in cur}


def _vol_panel(ticker: str, price, mos_floor, with_vol: bool):
    if not with_vol:
        return vol_panel_from_overlay(
            None, note="vol overlay off — toggle on to add the option-implied downside "
            "cross-check (NON-PRIMARY, data-only).")
    if not TastytradeProvider.available():
        return vol_panel_from_overlay(
            None, note="vol overlay skipped: set TASTYTRADE_CLIENT_SECRET / "
            "TASTYTRADE_REFRESH_TOKEN to enable (data-only — no positions, no orders).")
    try:
        vf = TastytradeProvider().get_volatility([ticker])
        d = volatility_overlay(vf, ticker, price=price, mos_floor=mos_floor)
        return vol_panel_from_overlay(d)
    except Exception as exc:  # noqa: BLE001 — overlay must never break the workup
        return vol_panel_from_overlay(None, note=f"vol overlay skipped: {exc}")


def for_ticker(ticker: str, peers=None, *, wacc=None, terminal_growth=None,
               forecast_years=None, share_basis="wad", fetch_prices=True,
               refresh=False, with_vol=False) -> NameWorkupView:
    res = run_comps(ticker, peers, wacc=wacc, terminal_growth=terminal_growth,
                    forecast_years=forecast_years, share_basis=share_basis,
                    fetch_prices=fetch_prices, refresh=refresh)
    s = res.subject

    cases = sorted(s.dcf_cases, key=lambda c: _CASE_ORDER.get(c.label, 9))
    bear_fv = next((c.fair_value_per_share for c in cases if c.label == "bear"), None)
    base_mos = s.base_margin_of_safety()
    bands = [_band_view(b) for b in (s.history.bands.values() if s.history else [])]

    comps_artifacts = {}
    if res.xlsx_path:
        comps_artifacts["xlsx"] = artifacts.register(res.xlsx_path, "xlsx")
    if res.md_path and res.md_path.exists():
        comps_artifacts["md"] = artifacts.register(res.md_path, "md")

    model_path = models_dir() / f"{ticker.upper()}_model.xlsx"
    model_artifact = artifacts.register(model_path, "xlsx") if model_path.exists() else None

    return NameWorkupView(
        ticker=s.ticker,
        name=clean_scalar(s.name),
        is_bank=bool(s.is_bank),
        market_cap=fig(s.market_cap, YFINANCE, fmt="ccy"),
        price=fig(s.price, YFINANCE, fmt="ccy"),
        current=_current_view(s.current or {}),
        quality=_quality_view(s.quality or {}),
        base_margin_of_safety=fig(base_mos, MIXED, fmt="pct",
                                  flag="risk" if (base_mos is not None and base_mos == base_mos and base_mos < 0) else None),
        base_implied_growth=fig(s.base_implied_growth(), MIXED, fmt="pct"),
        dcf_cases=[_dcf_case_view(c) for c in cases],
        normalized=_normalized_view(s.normalized),
        bands=bands,
        vol_panel=_vol_panel(s.ticker, s.price, bear_fv, with_vol),
        model_artifact=model_artifact,
        comps_artifacts=comps_artifacts,
        peers=[p.ticker for p in res.peers],
        peers_source="config" if peers is None else "explicit",
        wacc=fig(res.wacc, MIXED, fmt="pct"),
        terminal_growth=fig(res.terminal_growth, MIXED, fmt="pct"),
        sources=split_sources(s.sources),
        error=clean_scalar(s.error),
    )

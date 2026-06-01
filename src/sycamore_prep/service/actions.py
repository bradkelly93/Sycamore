"""Action wrappers — the WRITE / long-running paths (Phase B + C).

Phase A was read-only; this module adds the safe single-name triggers (build the
Excel model, run the vol cross-check, track one spin-off) and the two heavy jobs
(rebuild the universe, run the full pipeline) that ``service.jobs`` executes in the
background. As always: no analytics here — these call the engine entry points and
normalize the result into a view-model. All the §3 rules still hold.
"""

from __future__ import annotations

from ..adapters import TastytradeProvider
from ..config import cache_dir, models_dir
from ..models import build_models
from ..pipeline import run_pipeline
from ..spinoffs.tracker import run_track
from ..universe.builder import build_universe
from . import artifacts
from .overlays import vol_panel_from_overlay
from .serde import clean_scalar, split_flags
from .viewmodels import ActionResult, ArtifactRef, fig

MIXED = "edgar + yfinance price (non-primary)"


# --------------------------------------------------------------------------- #
# Phase B — fast, inline single-name actions
# --------------------------------------------------------------------------- #
def build_model(ticker: str, peers=None, *, wacc=None, terminal_growth=None,
                forecast_years=None, share_basis="wad", spinco=None,
                refresh=False) -> ActionResult:
    """Generate the Excel model scaffold for one name (models/<T>_model.xlsx)."""
    ticker = ticker.upper()
    try:
        res = build_models(ticker, peers, wacc=wacc, terminal_growth=terminal_growth,
                           forecast_years=forecast_years, share_basis=share_basis,
                           spinco=spinco, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 — surface, never 500
        return ActionResult(ok=False, title=f"Build model — {ticker}",
                            message=f"{type(exc).__name__}: {exc}")
    ref = artifacts.register(res.xlsx_path, "xlsx")
    variant = "bank (P/TBV + normalized EPS)" if res.is_bank else "non-bank (FCFF DCF)"
    sotp = " · spin-off SOTP tab included" if res.has_sotp else ""
    s = res.comps_result.subject
    detail = {
        "Margin of safety (base)": fig(s.base_margin_of_safety(), MIXED, fmt="pct",
                                       flag="risk" if (s.base_margin_of_safety() == s.base_margin_of_safety()
                                                       and s.base_margin_of_safety() < 0) else None),
        "Implied growth (base)": fig(s.base_implied_growth(), MIXED, fmt="pct"),
    }
    return ActionResult(
        ok=True, title=f"Excel model built — {ticker}",
        message=f"{variant}{sotp}. Download it and flex the bear column in Excel "
                "(the tool never re-creates the live formulas in the browser).",
        artifacts=[ref] if ref else [], detail=detail, link=f"/workup/{ticker}")


def run_vol(ticker: str, *, mos_floor=None) -> ActionResult:
    """Run the option-implied volatility cross-check for one name (NON-PRIMARY)."""
    ticker = ticker.upper()
    if not TastytradeProvider.available():
        return ActionResult(
            ok=False, title=f"Volatility — {ticker}",
            message="Volatility skipped: set TASTYTRADE_CLIENT_SECRET / "
                    "TASTYTRADE_REFRESH_TOKEN to enable (data-only — no positions, no orders).")
    try:
        from ..metrics.volatility import volatility_overlay
        vf = TastytradeProvider().get_volatility([ticker])
        panel = vol_panel_from_overlay(volatility_overlay(vf, ticker, mos_floor=mos_floor))
    except Exception as exc:  # noqa: BLE001
        return ActionResult(ok=False, title=f"Volatility — {ticker}",
                            message=f"Volatility skipped: {exc}")
    detail = {
        "IV rank": panel.iv_rank, "Expected move (30d)": panel.expected_move_30d_pct,
        "1σ downside price": panel.sigma_down_30d_price,
    }
    return ActionResult(
        ok=True, title=f"Volatility cross-check — {ticker} (NON-PRIMARY)",
        message="Option-implied risk read. Context only — never feeds the score or rank. "
                + ("Flags: " + ", ".join(panel.vol_flags) if panel.vol_flags else "No flags."),
        detail={k: v for k, v in detail.items() if v is not None}, link=f"/workup/{ticker}")


def track_spinoff(parent: str, spinco: str | None = None, *, refresh=False) -> ActionResult:
    """Track one parent/spinco pair (live EDGAR scan for one name)."""
    parent = parent.upper()
    try:
        res = run_track(parent, spinco=spinco, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(ok=False, title=f"Spin-off track — {parent}",
                            message=f"{type(exc).__name__}: {exc}")
    refs = [r for r in (artifacts.register(res.xlsx_path, "xlsx") if res.xlsx_path else None,
                        artifacts.register(res.md_path, "md") if res.md_path else None) if r]
    n = len(res.records)
    flags = sorted({f for rec in res.records for f in getattr(rec, "downside_flags", [])})
    return ActionResult(
        ok=True, title=f"Spin-off tracked — {parent}",
        message=f"{n} record(s) found." + (f" Downside flags: {', '.join(flags)}." if flags else ""),
        artifacts=refs, link="/spinoffs")


# --------------------------------------------------------------------------- #
# Phase C — heavy jobs (run via service.jobs in a background thread)
# --------------------------------------------------------------------------- #
def rebuild_universe_job() -> dict:
    """Background job: rebuild data/cache/universe.parquet from the iShares CSVs +
    the committed Sycamore overlay. Returns a small summary + the CSV download."""
    df = build_universe()
    owned = int(df["owned_by_sycamore"].sum()) if "owned_by_sycamore" in df.columns else 0
    ref = artifacts.register(cache_dir() / "universe.csv", "csv")
    return {
        "summary": {"companies": int(len(df)), "sycamore_owned": owned},
        "artifact": ref,
        "url": "/universe",
    }


def run_pipeline_job(tickers=None, *, sector=None, sycamore_only=False, top=10,
                     wacc=None, terminal_growth=None, forecast_years=None,
                     auto_peers=True, refresh=False) -> dict:
    """Background job: run the full funnel and return a link to the new run."""
    res = run_pipeline(tickers=tickers, sector=sector, sycamore_only=sycamore_only,
                       top=top, wacc=wacc, terminal_growth=terminal_growth,
                       forecast_years=forecast_years, auto_peers=auto_peers, refresh=refresh)
    run_ref = artifacts.register(res.out_dir, "folder")
    n_err = sum(1 for d in res.dossiers if d.error)
    return {
        "summary": {"shortlist": len(res.shortlist), "dossiers": len(res.dossiers),
                    "errors": n_err, "run": res.out_dir.name},
        "url": f"/pipeline/{run_ref.token}" if run_ref else "/pipeline",
    }

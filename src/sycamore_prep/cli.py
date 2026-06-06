"""Typer CLI. Phase 1 wires up: pull-fundamentals, show-financials, build-universe."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import typer

from .adapters import EdgarProvider, PolymarketProvider, YFinanceProvider
from .comps import run_comps
from .config import cache_dir, load_config
from .models import build_models
from .pipeline import run_pipeline
from .prediction_markets import (
    build_overlay,
    discover_for_ticker,
    empty_mapping,
    load_mapping,
    save_mapping,
    upsert,
    write_overlay,
)
from .screener import run_screener
from .spinoffs.tracker import load_tracker, run_scan, run_track
from .universe.builder import build_universe, load_universe


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Sycamore interview prep toolkit. See CLAUDE.md for the operating philosophy.",
)


@app.command("pull-fundamentals")
def pull_fundamentals(
    tickers: list[str] = typer.Argument(..., help="One or more tickers (e.g., CW WES UMBF)"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass cache and re-pull."),
) -> None:
    """Pull and cache SEC EDGAR XBRL company-facts for each ticker."""
    edgar = EdgarProvider()
    for t in tickers:
        try:
            ff = edgar.get_financials(t, use_cache=not refresh)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"[{t}] FAILED: {exc}", fg=typer.colors.RED)
            continue
        n_rows = len(ff.df)
        n_concepts = ff.df["concept"].nunique()
        typer.secho(
            f"[{t}] cached {n_rows} rows across {n_concepts} concepts → {cache_dir()}",
            fg=typer.colors.GREEN,
        )


@app.command("show-financials")
def show_financials(
    ticker: str = typer.Argument(...),
    concept: str = typer.Option("Revenues", "--concept", "-c"),
    fp: str = typer.Option("FY", "--fp", help="Fiscal period: FY, Q1, Q2, Q3, Q4."),
    n: int = typer.Option(10, "--last", "-n", help="Show last N periods."),
) -> None:
    """Print the cached series for a single concept. Tag = source column."""
    edgar = EdgarProvider()
    ff = edgar.get_financials(ticker)
    sub = ff.concept(concept, fp=fp).tail(n)
    if sub.empty:
        typer.secho(
            f"No data for {ticker} concept={concept} fp={fp}. "
            f"Try one of: {sorted(ff.df['concept'].unique())}",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    typer.echo(sub.to_string(index=False))


@app.command("build-universe")
def build_universe_cmd(
    iws: Path = typer.Option(None, "--iws", help="Override IWS holdings CSV path."),
    iwn: Path = typer.Option(None, "--iwn", help="Override IWN holdings CSV path."),
    sycamore: Path = typer.Option(None, "--sycamore", help="Override Sycamore overlay CSV path."),
) -> None:
    """Build the investable universe from IWS + IWN (+ Sycamore overlay)."""
    df = build_universe(iws_path=iws, iwn_path=iwn, sycamore_path=sycamore)
    out_dir = cache_dir()
    typer.secho(
        f"Universe: {len(df)} tickers "
        f"(IWS={int(df['in_iws'].sum())}, IWN={int(df['in_iwn'].sum())}, "
        f"Sycamore overlay={int(df['owned_by_sycamore'].sum())})",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"Wrote {out_dir/'universe.parquet'} and {out_dir/'universe.csv'}")


@app.command("show-universe")
def show_universe(
    n: int = typer.Option(20, "--n", help="Rows to print."),
    sycamore_only: bool = typer.Option(False, "--sycamore-only"),
) -> None:
    """Print rows from the cached universe."""
    df = load_universe()
    if df is None:
        typer.secho("No universe cached. Run `build-universe` first.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    if sycamore_only:
        df = df[df["owned_by_sycamore"]]
    typer.echo(df.head(n).to_string(index=False))


@app.command("screen")
def screen_cmd(
    tickers: list[str] = typer.Argument(
        None, help="Optional explicit tickers; if omitted, runs against the cached universe."
    ),
    sector: str = typer.Option(
        None, "--sector", help="Filter universe by GICS sector substring (e.g., 'banks', 'industrials')."
    ),
    limit: int = typer.Option(None, "--limit", help="Cap candidate count (useful for first runs)."),
    output: Path = typer.Option(None, "--output", "-o", help="Custom xlsx output path."),
    skip_market_cap: bool = typer.Option(
        False, "--skip-market-cap",
        help="Skip yfinance market-cap fetch; quality + improving scores only.",
    ),
    hard_exclude: bool = typer.Option(
        False, "--hard-exclude-negative-space",
        help="Remove flagged names from the ranking entirely. Default keeps "
             "them visible (sub-scores shown) but sorted to the bottom.",
    ),
    vol: bool = typer.Option(
        False, "--vol",
        help="Annotate with the tastytrade volatility overlay (downside "
             "cross-check). Needs TASTYTRADE_CLIENT_SECRET/REFRESH_TOKEN; "
             "never scored.",
    ),
    tv_overlay: bool = typer.Option(
        False, "--tv-overlay",
        help="Append your TradingView technical-screen membership + divergence "
             "flags as a NON-PRIMARY overlay. Never enters the score or rank. "
             "Requires tradingview.enabled + filters in config.yaml.",
    ),
    refresh_tv: bool = typer.Option(
        False, "--refresh-tv",
        help="Bypass the cached TradingView screen snapshot and re-pull.",
    ),
    with_prediction_overlay: bool = typer.Option(
        False, "--with-prediction-overlay",
        help="Annotate each name with its top prediction-market event "
             "(event_* columns). NON-PRIMARY; never affects composite_rank. "
             "Needs confirmed mappings + Polymarket egress.",
    ),
) -> None:
    """Run the three-attribute quality-value screener.

    Sub-scores (Q1 Quality, Q2 Valuation, Q3 Improving Fundamentals) are
    reported separately per CLAUDE.md — composite_rank is only the sort key.
    With --vol, IV rank/percentile + expected-move columns are appended as a
    downside cross-check; they annotate, never move the score or rank.
    """
    df = run_screener(
        tickers=tickers or None,
        sector=sector,
        limit=limit,
        output_path=output,
        skip_market_cap=skip_market_cap,
        hard_exclude_neg_space=hard_exclude,
        with_vol=vol,
        tv_overlay=tv_overlay,
        refresh_tv=refresh_tv,
        with_prediction_overlay=with_prediction_overlay,
    )
    out = output or (cache_dir() / "screener_output.xlsx")
    typer.secho(f"Scored {len(df)} tickers → {out}", fg=typer.colors.GREEN)
    cols_to_show = [c for c in [
        "name", "composite_rank", "q1_quality_score",
        "q2_valuation_score", "q3_improving_score",
        "negative_space", "ns_flags",
        "iv_rank", "iv_percentile", "expected_move_30d_pct", "vol_flags",
        "passes_screen", "tv_divergence",
        "event_top_prob", "event_contradiction",
    ] if c in df.columns]
    typer.echo(df[cols_to_show].head(20).to_string())
    # Surface any overlay skip notes (vol / TradingView / prediction) so a
    # missing-creds / disabled / egress-blocked overlay is an explicit,
    # actionable message — never a silent no-op.
    for key in ("vol_note", "tv_note", "prediction_note"):
        note = df.attrs.get(key)
        if note:
            typer.secho(note, fg=typer.colors.YELLOW)


@app.command("vol")
def vol_cmd(
    tickers: list[str] = typer.Argument(..., help="One or more tickers (e.g., CW WES)."),
    price: float = typer.Option(
        None, "--price", help="Spot price — enables the $ downside + margin-of-safety check."
    ),
    mos_floor: float = typer.Option(
        None, "--mos-floor",
        help="Margin-of-safety floor price. Flags when the option-implied "
             "1-sigma-down price punctures it (downside cross-check).",
    ),
    horizon: int = typer.Option(
        None, "--horizon-days", help="Expected-move horizon in calendar days (default from config)."
    ),
    skew: bool = typer.Option(
        False, "--skew",
        help="Also fetch 25-delta put skew via the dxLink Greeks stream "
             "(slower; one websocket per ticker; needs the optional "
             "'websockets' dependency).",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass today's cache and re-pull."),
) -> None:
    """Per-ticker volatility overlay (downside cross-check) from tastytrade.

    DATA ONLY — reads market-level IV metrics for the named tickers; never
    positions, never orders. Set TASTYTRADE_CLIENT_SECRET / TASTYTRADE_REFRESH_TOKEN
    in the environment (OAuth2; never config.yaml).
    """
    from .adapters import TastytradeProvider
    from .metrics.volatility import volatility_overlay

    if not TastytradeProvider.available():
        typer.secho(
            "Set TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN in your "
            "environment first (OAuth2; never config.yaml). Create them under "
            "'OAuth Applications' in your tastytrade account.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    horizon_days = horizon or load_config().tastytrade.horizon_days
    provider = TastytradeProvider()
    try:
        vf = provider.get_volatility([t.upper() for t in tickers], use_cache=not refresh)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"tastytrade fetch failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    rows = [
        volatility_overlay(vf, t, price=price, mos_floor=mos_floor, horizon_days=horizon_days)
        for t in tickers
    ]
    df = pd.DataFrame(rows)
    if df.empty or df["vol_source"].eq("").all():
        typer.secho(
            "No vol data returned (check tickers / market hours / API access).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    df["vol_flags"] = df["vol_flags"].apply(lambda v: ", ".join(v) if isinstance(v, list) else "")

    if skew:
        skews: dict[str, float | None] = {}
        for t in tickers:
            try:
                res = provider.put_skew(t, target_days=horizon_days)
                skews[t.upper()] = round(res["put_skew_25d"], 4) if res else None
            except Exception as exc:  # noqa: BLE001 — streaming is best-effort
                typer.secho(f"  skew[{t}] unavailable: {exc}", fg=typer.colors.YELLOW)
                skews[t.upper()] = None
        df["put_skew_25d"] = df["ticker"].map(skews)

    show = [c for c in [
        "ticker", "iv_rank", "iv_percentile", "iv_index", "iv_hv_30_day_diff",
        "expected_move_30d_pct", "expected_move_earnings_pct",
        "days_to_earnings", "next_earnings_date",
        "sigma_down_30d_price", "put_skew_25d",
        "vol_beta", "liquidity_rating", "vol_flags",
    ] if c in df.columns]
    typer.echo(df[show].to_string(index=False))
    typer.secho("source: tastytrade — downside cross-check only", fg=typer.colors.GREEN)


@app.command("comps")
def comps_cmd(
    ticker: str = typer.Argument(..., help="Subject ticker (e.g., CW)."),
    peers: str = typer.Option(
        None, "--peers", help="Comma-separated peers; default from config.yaml peers map."
    ),
    wacc: float = typer.Option(None, "--wacc", help="Override WACC (default config valuation.wacc)."),
    terminal_growth: float = typer.Option(
        None, "--terminal-growth", help="Override perpetuity growth (default config)."
    ),
    years: int = typer.Option(None, "--years", help="Forecast horizon (default config forecast_years)."),
    share_basis: str = typer.Option(
        "wad", "--share-basis", help="Historical share basis: wad | shares_out | eps_implied."
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="Custom xlsx path (the .md tear-sheet is written alongside)."
    ),
    no_prices: bool = typer.Option(
        False, "--no-prices", help="Skip price fetch; own-history bands blank, comps still run."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass caches; re-pull fundamentals + prices."),
) -> None:
    """Peer comps + own-history multiple bands + normalized earnings + reverse DCF.

    The three valuation lenses (discount-to-own-history, peer-relative, and
    reverse-DCF implied growth) are reported separately per CLAUDE.md, with the
    downside / margin-of-safety surfaced alongside. Writes comps_<TICKER>.xlsx
    and comps_<TICKER>.md.
    """
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()] if peers else None
    res = run_comps(
        ticker, peer_list, wacc=wacc, terminal_growth=terminal_growth,
        forecast_years=years, share_basis=share_basis, output_path=output,
        fetch_prices=not no_prices, refresh=refresh,
    )
    s = res.subject
    typer.secho(f"Comps for {s.ticker} → {res.xlsx_path}", fg=typer.colors.GREEN)
    typer.echo(f"  markdown tear-sheet → {res.md_path}")
    if s.error:
        typer.secho(f"  WARNING: {s.error}", fg=typer.colors.YELLOW)
    for name, b in (s.history.bands.items() if (s.history and s.history.bands) else []):
        cur = f"{b.current:.2f}" if b.current == b.current else "n/a"
        pct = f"{b.percentile_cheap:.0f}" if b.percentile_cheap == b.percentile_cheap else "n/a"
        typer.echo(f"  {name:<10} current={cur}  cheap-vs-own-history %ile={pct}  (N={b.n})")
    g, mos = s.base_implied_growth(), s.base_margin_of_safety()
    if g == g:
        typer.echo(f"  reverse DCF (base): implied FCFF growth {g * 100:.1f}%"
                   + (f"  |  margin of safety {mos * 100:.1f}%" if mos == mos else ""))
    elif s.is_bank:
        typer.echo("  reverse DCF: N/A (bank)")


@app.command("build-models")
def build_models_cmd(
    ticker: str = typer.Argument(..., help="Subject ticker (e.g., CW)."),
    peers: str = typer.Option(
        None, "--peers", help="Comma-separated peers; default from config.yaml peers map."
    ),
    wacc: float = typer.Option(None, "--wacc", help="Override WACC (default config valuation.wacc)."),
    terminal_growth: float = typer.Option(
        None, "--terminal-growth", help="Override perpetuity growth (default config)."
    ),
    years: int = typer.Option(None, "--years", help="Forecast horizon (default config forecast_years)."),
    bank: bool = typer.Option(
        None, "--bank/--no-bank", help="Override bank auto-detection (default: auto via Deposits tag)."
    ),
    spinco: str = typer.Option(
        None, "--spinco", help="SpinCo ticker/CIK → adds the SOTP tab via the spin-off tracker."
    ),
    share_basis: str = typer.Option(
        "wad", "--share-basis", help="Historical share basis: wad | shares_out | eps_implied."
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="Custom xlsx path (default models/<TICKER>_model.xlsx)."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass caches; re-pull fundamentals + prices."),
) -> None:
    """Build the Excel model scaffold for a ticker → models/<TICKER>_model.xlsx.

    A LEAN, rebuildable skeleton: drivers + LIVE Excel formulas seeded from the
    comps / spin-off engines so the reverse-DCF ties to `comps <TICKER>` and the
    bear page flexes in Excel. NOT a finished model — see models/MODEL_NOTES.md.
    Open in Excel to confirm formulas compute (openpyxl does not evaluate them).
    """
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()] if peers else None
    res = build_models(
        ticker, peer_list, wacc=wacc, terminal_growth=terminal_growth,
        forecast_years=years, share_basis=share_basis, bank=bank, spinco=spinco,
        output_path=output, refresh=refresh,
    )
    typer.secho(f"Model scaffold for {res.ticker} → {res.xlsx_path}", fg=typer.colors.GREEN)
    typer.echo(f"  variant: {'bank (P/TBV + normalized EPS)' if res.is_bank else 'non-bank (FCFF DCF)'}"
               + ("  ·  SOTP tab included" if res.has_sotp else ""))
    s = res.comps_result.subject
    if s.error:
        typer.secho(f"  WARNING: {s.error}", fg=typer.colors.YELLOW)
    g, mos = s.base_implied_growth(), s.base_margin_of_safety()
    if g == g:
        typer.echo(f"  reverse DCF (base): implied FCFF growth {g * 100:.1f}%"
                   + (f"  |  margin of safety {mos * 100:.1f}%" if mos == mos else ""))
    elif res.is_bank:
        typer.echo("  reverse DCF: N/A (bank) — P/TBV + normalized EPS instead")
    typer.echo("  Open in Excel: confirm no #REF!/#DIV0!, the reverse-DCF residual ≈ 0, "
               "and the bear column flexes. Re-solve implied growth with Goal Seek.")


@app.command("pipeline")
def pipeline_cmd(
    tickers: list[str] = typer.Argument(
        None, help="Explicit tickers; if omitted, screens the universe and takes the top-N."
    ),
    sector: str = typer.Option(None, "--sector", help="Restrict the funnel to a GICS sector substring."),
    sycamore_only: bool = typer.Option(
        False, "--sycamore-only", help="Only names in the Sycamore-owned overlay."
    ),
    top: int = typer.Option(10, "--top", help="How many shortlisted names to fully work up."),
    wacc: float = typer.Option(None, "--wacc", help="Override WACC (default config)."),
    terminal_growth: float = typer.Option(None, "--terminal-growth", help="Override perpetuity growth."),
    years: int = typer.Option(None, "--years", help="Forecast horizon (default config)."),
    no_auto_peers: bool = typer.Option(
        False, "--no-auto-peers", help="Disable auto peer derivation; use only config.peers."
    ),
    link_spinoffs: bool = typer.Option(
        False, "--link-spinoffs", help="Flag shortlisted names that appear in a recent spin-off scan."
    ),
    skip_market_cap: bool = typer.Option(
        False, "--skip-market-cap", help="Skip yfinance market-cap fetch in the screen step."
    ),
    output: Path = typer.Option(None, "--output", "-o", help="Run-folder path (default data/cache/pipeline_<ts>)."),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass caches; re-pull fundamentals + prices."),
) -> None:
    """End-to-end funnel: universe → screen → shortlist → comps + model per name.

    Writes a self-contained run folder: one dossier per name (comps tear-sheet +
    model workbook) plus a downside-first ranked index (xlsx + md) and a run
    manifest. Selection modes compose (top-N / --sycamore-only / --sector /
    explicit tickers). Cache-backed, so re-runs only re-pull what's missing.
    """
    res = run_pipeline(
        tickers=list(tickers) if tickers else None,
        sector=sector, sycamore_only=sycamore_only, top=top,
        wacc=wacc, terminal_growth=terminal_growth, forecast_years=years,
        auto_peers=not no_auto_peers, link_spinoffs=link_spinoffs,
        skip_market_cap=skip_market_cap, output_dir=output, refresh=refresh,
    )
    typer.secho(f"Worked up {len(res.dossiers)} name(s) → {res.out_dir}", fg=typer.colors.GREEN)
    typer.echo(f"  ranked index → {res.index_xlsx}")
    typer.echo(f"  manifest     → {res.manifest_path}")
    for d in res.dossiers:
        mos = f"{d.margin_of_safety_base * 100:.1f}%" if isinstance(d.margin_of_safety_base, float) \
            and d.margin_of_safety_base == d.margin_of_safety_base else "n/a"
        tag = "BANK" if d.is_bank else "    "
        flag = f"  ⚠ {d.ns_flags}" if d.ns_flags else ""
        err = f"  ERROR: {d.error}" if d.error else ""
        typer.echo(f"  [{tag}] {d.ticker:<6} base MoS {mos:>7}  peers: {d.peers_used or 'none'}{flag}{err}")


spin_app = typer.Typer(
    no_args_is_help=True,
    help="Spin-off tracker (special-situations value). Downside-first; the three "
         "attributes are reported separately. See CLAUDE.md.",
)
app.add_typer(spin_app, name="spinoffs")


@spin_app.command("scan")
def spinoffs_scan(
    lookback_days: int = typer.Option(
        None, "--lookback-days", help="Window for recent 10-12B filings (default config)."
    ),
    forms: str = typer.Option(None, "--forms", help="Comma-separated forms (default config: 10-12B)."),
    query: str = typer.Option(None, "--query", help="Full-text query (default config)."),
    limit: int = typer.Option(None, "--limit", help="Cap candidate count."),
    output: Path = typer.Option(None, "--output", "-o", help="Custom xlsx path (.md alongside)."),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass caches; re-hit EDGAR."),
) -> None:
    """Scan recent Form 10 / 10-12B registrations market-wide (discovery).

    Broad + fast: metadata + status only. Run `spinoffs track` for a SpinCo's
    leverage/quality flags.
    """
    form_list = [f.strip() for f in forms.split(",") if f.strip()] if forms else None
    res = run_scan(forms=form_list, lookback_days=lookback_days, query=query,
                   limit=limit, output_path=output, refresh=refresh)
    typer.secho(
        f"Found {len(res.records)} Form-10 registrations → {res.xlsx_path}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  markdown → {res.md_path}")
    cols = [c for c in ["spinco_name", "spinco_ticker", "status",
                        "first_form10_date", "amendment_count", "sic"] if c in res.df.columns]
    if not res.df.empty:
        typer.echo(res.df[cols].head(25).to_string())


@spin_app.command("track")
def spinoffs_track(
    parent: str = typer.Argument(..., help="Parent ticker (e.g., DHR, MMM)."),
    spinco: str = typer.Option(
        None, "--spinco", help="SpinCo ticker or CIK for deterministic linkage."
    ),
    output: Path = typer.Option(None, "--output", "-o", help="Custom xlsx path (.md alongside)."),
    no_softfields: bool = typer.Option(
        False, "--no-softfields", help="Skip parsing the 10-12B for ratio/dates."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass caches; re-hit EDGAR."),
) -> None:
    """Track a parent's spin-off: leverage/quality flags + soft fields.

    The three attributes (better business / valuation disparity / improving
    fundamentals) are reported separately; downside flags + 'review' prompts
    surface first. Writes spinoffs_<PARENT>.xlsx and .md.
    """
    res = run_track(parent, spinco=spinco, output_path=output, refresh=refresh,
                    fetch_softfields=not no_softfields)
    typer.secho(
        f"Tracked {len(res.records)} spin(s) for {parent.upper()} → {res.xlsx_path}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  markdown tear-sheet → {res.md_path}")
    for r in res.records:
        if r.downside_flags:
            ds = ", ".join(r.downside_flags)
        else:
            ds = "none (financials loaded)" if r.has_financials else "n/a (no XBRL yet — pending)"
        bits = []
        if r.net_debt_ebitda is not None:
            nde = 0.0 if abs(r.net_debt_ebitda) < 0.05 else r.net_debt_ebitda
            bits.append(f"net debt/EBITDA {nde:.1f}x"
                        + (" (net cash)" if r.net_debt_ebitda < 0 else ""))
        if r.distribution_ratio:
            bits.append(f"ratio {r.distribution_ratio}")
        if r.distribution_date:
            bits.append(f"distributed {r.distribution_date}")
        extra = ("  ·  " + " · ".join(bits)) if bits else ""
        typer.echo(f"  {r.spinco_name or r.spinco_ticker or r.spinco_cik} "
                   f"[{r.status}]  downside: {ds}{extra}")
        if r.error:
            typer.secho(f"    WARNING: {r.error}", fg=typer.colors.YELLOW)


@spin_app.command("show")
def spinoffs_show(
    n: int = typer.Option(25, "--n", help="Rows to print."),
) -> None:
    """Print the last scan/track result frame (cached)."""
    df = load_tracker()
    if df is None or df.empty:
        typer.secho("No cached tracker output. Run `spinoffs scan` or `spinoffs track` first.",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    cols = [c for c in ["spinco", "spinco_ticker", "status", "downside_flags",
                        "first_form10_date", "distribution_ratio"] if c in df.columns]
    typer.echo(df[cols].head(n).to_string(index=False))


def _resolve_overlay_targets(
    tickers: list[str] | None,
    universe: bool,
    limit: int | None,
    sector_resolver: Callable[[str], str | None] | None = None,
) -> list[tuple[str, str, str | None]]:
    """Return [(ticker, name, gics_sector)] for discovery.

    Universe rows already carry `gics_sector`. For explicitly typed tickers (or
    the config `test_tickers` fallback) the sector isn't known up front; when a
    `sector_resolver` is supplied it's looked up so sector-scoped macro/industry
    markets read through on explicit-ticker runs, not just `--universe`.
    Resolution failures degrade to None (same as before)."""
    def _sector(t: str) -> str | None:
        if sector_resolver is None:
            return None
        try:
            return sector_resolver(t)
        except Exception:  # noqa: BLE001 — sector lookup is best-effort
            return None

    if tickers:
        return [(t.upper(), t.upper(), _sector(t.upper())) for t in tickers]
    df = load_universe() if universe else None
    if df is None:
        cfg = load_config()
        return [(t, t, _sector(t)) for t in cfg.test_tickers]
    if limit:
        df = df.head(limit)
    return [
        (str(r["ticker"]), str(r.get("name", r["ticker"])), r.get("gics_sector"))
        for _, r in df.iterrows()
    ]


@app.command("prediction-discover")
def prediction_discover(
    tickers: list[str] = typer.Argument(
        None, help="Tickers; omit to use the cached universe (or config test_tickers)."
    ),
    universe: bool = typer.Option(False, "--universe", help="Discover for the whole cached universe."),
    apertures: str = typer.Option(
        "company,peer,industry,macro", "--apertures",
        help="Comma list of apertures: company, peer, industry, macro.",
    ),
    min_relevance: float = typer.Option(
        None, "--min-relevance", help="Override config polymarket.min_relevance."
    ),
    limit: int = typer.Option(None, "--limit", help="Cap tickers processed (universe mode)."),
) -> None:
    """Search Polymarket per name and UPSERT candidate matches into the editable
    mapping CSV (data/raw/prediction_markets.csv).

    Each row gets a transparent relevance_score; human-confirmed rows are never
    overwritten. Review and set confirmed=True before running prediction-overlay.
    Requires Polymarket egress (blocked in the Claude-on-the-web sandbox).
    """
    cfg = load_config()
    provider = PolymarketProvider()
    aps = [a.strip().lower() for a in apertures.split(",") if a.strip()]
    minrel = min_relevance if min_relevance is not None else cfg.polymarket.min_relevance

    # The industry + macro apertures read through a name's GICS sector. Universe
    # rows already carry it; for typed tickers, resolve it via yfinance so those
    # apertures fire on explicit-ticker runs too. Only build the resolver when a
    # sector-dependent aperture is requested (avoids needless network calls).
    sector_resolver = None
    if {"industry", "macro"} & set(aps):
        sector_resolver = YFinanceProvider().get_sector

    found: list[pd.DataFrame] = []
    for ticker, name, sector in _resolve_overlay_targets(
        tickers, universe, limit, sector_resolver
    ):
        try:
            cands = discover_for_ticker(
                provider, ticker, name, sector, cfg, apertures=aps, min_relevance=minrel
            )
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"[{ticker}] discovery FAILED: {exc}", fg=typer.colors.RED)
            continue
        n = 0 if cands.empty else len(cands)
        if not cands.empty:
            found.append(cands)
        typer.secho(f"[{ticker}] {n} candidate market(s)", fg=typer.colors.GREEN)

    new = pd.concat(found, ignore_index=True) if found else empty_mapping()
    merged = upsert(load_mapping(), new)
    path = save_mapping(merged)
    n_unconfirmed = int((~merged["confirmed"]).sum())
    typer.secho(f"Mapping: {len(merged)} rows ({n_unconfirmed} unconfirmed) → {path}",
                fg=typer.colors.GREEN)
    typer.echo("Edit the CSV (set confirmed=True, fix event_type/direction) before the overlay.")


@app.command("prediction-overlay")
def prediction_overlay_cmd(
    tickers: list[str] = typer.Argument(None, help="Optional tickers; omit for all mapped names."),
    confirmed_only: bool = typer.Option(
        True, "--confirmed-only/--include-unconfirmed",
        help="Only use human-confirmed mappings (default), or include all candidates.",
    ),
    min_relevance: float = typer.Option(0.0, "--min-relevance", help="Drop mappings below this relevance."),
    include_macro: bool = typer.Option(
        False, "--include-macro",
        help="Include macro-aperture markets (segregated; context only, never bottom-up).",
    ),
    downside_only: bool = typer.Option(False, "--downside-only", help="Only RISK read-throughs."),
    output: Path = typer.Option(None, "--output", "-o", help="Custom xlsx output path."),
) -> None:
    """Pull current implied probabilities for mapped markets and write the
    event-risk overlay (xlsx + csv).

    Downside read-throughs sort to the top (downside-first). NON-PRIMARY signal
    — this output never feeds the screener composite. Requires Polymarket egress.
    """
    provider = PolymarketProvider()
    df = build_overlay(
        provider,
        tickers=tickers or None,
        confirmed_only=confirmed_only,
        min_relevance=min_relevance,
        include_macro=include_macro,
        downside_only=downside_only,
    )
    out = output or (cache_dir() / "prediction_overlay.xlsx")
    write_overlay(df, out)
    if df.empty:
        typer.secho(
            "No mapped markets matched. Run prediction-discover and confirm rows first "
            "(or pass --include-unconfirmed).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)
    typer.secho(
        f"Overlay: {len(df)} market(s) across {df['ticker'].nunique()} name(s) → {out}",
        fg=typer.colors.GREEN,
    )
    show = [c for c in [
        "ticker", "aperture", "read_through", "question",
        "implied_prob", "prob_chg_30d", "liquidity", "resolution_date", "source",
    ] if c in df.columns]
    typer.echo(df[show].head(25).to_string(index=False))


@app.command("config-check")
def config_check() -> None:
    """Sanity-check config + paths."""
    cfg = load_config()
    typer.echo(f"User-Agent: {cfg.edgar.user_agent}")
    typer.echo(f"Cache dir : {cache_dir()}")
    typer.echo(f"Peers configured for: {sorted(cfg.peers.keys())}")
    typer.echo(f"Test tickers       : {cfg.test_tickers}")
    typer.echo(f"Polymarket Gamma   : {cfg.polymarket.gamma_base_url}")
    typer.echo(f"Prediction mapping : {cfg.prediction.mapping_csv} (sectors: "
               f"{sorted(cfg.prediction.sector_keywords.keys())})")


@app.command("web")
def web(
    port: int = typer.Option(8010, "--port", "-p", help="Port to serve on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address — localhost only by default."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (development)."),
) -> None:
    """Launch the local Explorer UI (FastAPI + HTMX) and serve it in your browser.

    Localhost-only, single-user, no auth — reads the same env-based credentials as
    the CLI. Open the printed URL in your browser; press Ctrl+C to stop.
    """
    import uvicorn  # lazy: keeps offline CLI/tests free of the web stack

    typer.secho(
        f"Sycamore Explorer → http://{host}:{port}  (open in your browser; Ctrl+C to stop)",
        fg=typer.colors.GREEN,
    )
    uvicorn.run("sycamore_prep.web:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

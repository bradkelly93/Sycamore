"""Typer CLI. Phase 1 wires up: pull-fundamentals, show-financials, build-universe."""

from __future__ import annotations

from pathlib import Path

import typer

from .adapters import EdgarProvider
from .comps import run_comps
from .config import cache_dir, load_config
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
) -> None:
    """Run the three-attribute quality-value screener.

    Sub-scores (Q1 Quality, Q2 Valuation, Q3 Improving Fundamentals) are
    reported separately per CLAUDE.md — composite_rank is only the sort key.
    """
    df = run_screener(
        tickers=tickers or None,
        sector=sector,
        limit=limit,
        output_path=output,
        skip_market_cap=skip_market_cap,
        hard_exclude_neg_space=hard_exclude,
    )
    out = output or (cache_dir() / "screener_output.xlsx")
    typer.secho(f"Scored {len(df)} tickers → {out}", fg=typer.colors.GREEN)
    cols_to_show = [c for c in [
        "name", "composite_rank", "q1_quality_score",
        "q2_valuation_score", "q3_improving_score",
        "negative_space", "ns_flags",
    ] if c in df.columns]
    typer.echo(df[cols_to_show].head(20).to_string())


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
            bits.append(f"net debt/EBITDA {r.net_debt_ebitda:.1f}x")
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


@app.command("config-check")
def config_check() -> None:
    """Sanity-check config + paths."""
    cfg = load_config()
    typer.echo(f"User-Agent: {cfg.edgar.user_agent}")
    typer.echo(f"Cache dir : {cache_dir()}")
    typer.echo(f"Peers configured for: {sorted(cfg.peers.keys())}")
    typer.echo(f"Test tickers       : {cfg.test_tickers}")


if __name__ == "__main__":
    app()

"""Typer CLI. Phase 1 wires up: pull-fundamentals, show-financials, build-universe."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import typer

from .adapters import EdgarProvider
from .config import cache_dir, load_config
from .screener import run_screener
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
             "cross-check). Needs TASTYTRADE_USERNAME/PASSWORD; never scored.",
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
    )
    out = output or (cache_dir() / "screener_output.xlsx")
    typer.secho(f"Scored {len(df)} tickers → {out}", fg=typer.colors.GREEN)
    cols_to_show = [c for c in [
        "name", "composite_rank", "q1_quality_score",
        "q2_valuation_score", "q3_improving_score",
        "negative_space", "ns_flags",
        "iv_rank", "iv_percentile", "expected_move_30d_pct", "vol_flags",
    ] if c in df.columns]
    typer.echo(df[cols_to_show].head(20).to_string())
    note = df.attrs.get("vol_note")
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
    refresh: bool = typer.Option(False, "--refresh", help="Bypass today's cache and re-pull."),
) -> None:
    """Per-ticker volatility overlay (downside cross-check) from tastytrade.

    DATA ONLY — reads market-level IV metrics for the named tickers; never
    positions, never orders. Set TASTYTRADE_USERNAME / TASTYTRADE_PASSWORD in
    the environment (never config.yaml).
    """
    from .adapters import TastytradeProvider
    from .metrics.volatility import volatility_overlay

    if not TastytradeProvider.available():
        typer.secho(
            "Set TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD in your environment "
            "first (never config.yaml).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    horizon_days = horizon or load_config().tastytrade.horizon_days
    try:
        vf = TastytradeProvider().get_volatility(
            [t.upper() for t in tickers], use_cache=not refresh
        )
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
    show = [c for c in [
        "ticker", "iv_rank", "iv_percentile", "iv_index",
        "expected_move_30d_pct", "expected_move_earnings_pct",
        "days_to_earnings", "next_earnings_date",
        "sigma_down_30d_price", "vol_beta", "liquidity_rating", "vol_flags",
    ] if c in df.columns]
    typer.echo(df[show].to_string(index=False))
    typer.secho("source: tastytrade — downside cross-check only", fg=typer.colors.GREEN)


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

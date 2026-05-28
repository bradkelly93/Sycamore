"""Typer CLI. Phase 1 wires up: pull-fundamentals, show-financials, build-universe."""

from __future__ import annotations

from pathlib import Path

import typer

from .adapters import EdgarProvider
from .config import cache_dir, load_config
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

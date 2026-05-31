"""Typer CLI. Phase 1 wires up: pull-fundamentals, show-financials, build-universe."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import typer

from .adapters import EdgarProvider, PolymarketProvider, YFinanceProvider
from .config import cache_dir, load_config
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
    """
    df = run_screener(
        tickers=tickers or None,
        sector=sector,
        limit=limit,
        output_path=output,
        skip_market_cap=skip_market_cap,
        hard_exclude_neg_space=hard_exclude,
        with_prediction_overlay=with_prediction_overlay,
    )
    out = output or (cache_dir() / "screener_output.xlsx")
    typer.secho(f"Scored {len(df)} tickers → {out}", fg=typer.colors.GREEN)
    cols_to_show = [c for c in [
        "name", "composite_rank", "q1_quality_score",
        "q2_valuation_score", "q3_improving_score",
        "negative_space", "ns_flags",
        "event_top_prob", "event_contradiction",
    ] if c in df.columns]
    typer.echo(df[cols_to_show].head(20).to_string())


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


if __name__ == "__main__":
    app()

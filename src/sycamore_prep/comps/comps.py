"""Comps orchestrator: subject + peers -> peer-relative table + own-history
bands + normalized earnings + reverse DCF, written to xlsx + a markdown
tear-sheet.

Mirrors screener/screen.py: network-resilient (a peer that fails to load is
kept as a visible error row, never silently dropped), every figure source-
tagged, downside-first, and the three valuation lenses (own-history, peer-
relative, reverse-DCF) stay decomposed — never collapsed into one number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..adapters import EdgarProvider, YFinanceProvider
from ..adapters.base import FinancialsFrame
from ..config import cache_dir, load_config
from ..metrics import (
    ev_ebitda_fy,
    fcf_margin_fy,
    fcf_yield_fy,
    free_cash_flow_fy,
    is_bank,
    net_debt_fy,
    net_debt_to_ebitda_fy,
    p_tbv_fy,
    pe_fy,
    roic_fy,
)
from ..metrics.profitability import _series
from . import reverse_dcf as rdcf
from .history import HistoryResult, build_history
from .normalized import NormalizedEarnings, normalized_earnings


def _tail(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else float("nan")


def _last_shares(ff: FinancialsFrame) -> float:
    s = _series(ff, "WeightedAverageSharesDiluted").dropna()
    if s.empty:
        s = _series(ff, "SharesOutstanding").dropna()
    return float(s.iloc[-1]) if not s.empty else float("nan")


def _cagr(s: pd.Series, n: int) -> float:
    s = s.dropna()
    if n < 1 or len(s) < n + 1:
        return float("nan")
    a, b = float(s.iloc[-(n + 1)]), float(s.iloc[-1])
    if a <= 0 or a != a or b != b:
        return float("nan")
    return (b / a) ** (1.0 / n) - 1.0


@dataclass
class TickerAnalysis:
    ticker: str
    name: str
    is_bank: bool
    market_cap: float
    price: float
    current: dict = field(default_factory=dict)      # pe / ev_ebitda / fcf_yield / p_tbv
    quality: dict = field(default_factory=dict)       # roic / fcf_margin / net_debt_ebitda
    history: HistoryResult | None = None
    normalized: NormalizedEarnings | None = None
    dcf_cases: list = field(default_factory=list)     # list[rdcf.DcfCase]
    sources: str = ""
    error: str | None = None

    def own_history_pctile(self, band: str) -> float:
        if self.history and band in self.history.bands:
            return self.history.bands[band].percentile_cheap
        return float("nan")

    def base_implied_growth(self) -> float:
        for c in self.dcf_cases:
            if c.label == "base":
                return c.implied_growth
        return float("nan")

    def base_margin_of_safety(self) -> float:
        for c in self.dcf_cases:
            if c.label == "base":
                return c.margin_of_safety
        return float("nan")


def _current_market_cap(yfin: YFinanceProvider, ff: FinancialsFrame, ticker: str) -> tuple[float, float]:
    """Current market cap + price. Keeps the share count primary-source: when
    yfinance market cap is unavailable, fall back to price x EDGAR shares
    (mirrors the screener)."""
    shares = _last_shares(ff)
    price = mcap = None
    try:
        price = yfin.get_current_price(ticker)
    except Exception:  # noqa: BLE001 — yfinance is intentionally flaky
        price = None
    try:
        mcap = yfin.get_market_cap(ticker)
    except Exception:  # noqa: BLE001
        mcap = None
    if not mcap and price and shares == shares and shares > 0:
        mcap = price * shares
    if (not price or price != price) and mcap and shares == shares and shares > 0:
        price = mcap / shares
    return (float(mcap) if mcap else float("nan"),
            float(price) if price else float("nan"))


def _build_dcf(ff: FinancialsFrame, mcap: float, price: float, wacc: float, tg: float, years: int) -> list:
    fcf = free_cash_flow_fy(ff).dropna()
    if fcf.empty:
        return []
    fcf0_latest = float(fcf.iloc[-1])
    last5 = fcf.iloc[-5:]
    fcf0_trough = float(last5.min())
    fcf0_bull = float(max(fcf0_latest, last5.mean()))
    net_debt = _tail(net_debt_fy(ff))
    net_debt = 0.0 if net_debt != net_debt else net_debt
    shares = _last_shares(ff)
    if shares != shares or shares <= 0 or fcf0_latest <= 0:
        return []
    # Conservative forward anchor: the franchise's own realized FCF CAGR, but
    # capped safely below WACC (and at 10%) so a 10y compounding stage can't
    # approach the discount rate and explode the PV. Floored at terminal growth.
    # Downside-first: the reverse implied-growth read stays unclipped; only this
    # forward fair-value/MoS anchor is held conservative.
    g_hist = _cagr(fcf, min(5, len(fcf) - 1))
    cap = min(0.10, max(wacc - 0.02, tg))
    assumed = min(max(g_hist if g_hist == g_hist else tg, tg), cap)
    return rdcf.build_cases(
        current_market_cap=mcap, net_debt=net_debt, shares=shares,
        fcf0_base=fcf0_latest, wacc=wacc, terminal_growth=tg, years=years,
        assumed_growth=assumed, fcf0_bear=fcf0_trough, fcf0_bull=fcf0_bull,
        current_price=price,
    )


def analyze(
    edgar: EdgarProvider,
    yfin: YFinanceProvider | None,
    ticker: str,
    name: str,
    *,
    wacc: float,
    terminal_growth: float,
    years: int,
    share_basis: str,
    fetch_prices: bool,
    refresh: bool,
) -> TickerAnalysis:
    try:
        ff = edgar.get_financials(ticker, use_cache=not refresh)
    except Exception as exc:  # noqa: BLE001
        return TickerAnalysis(ticker, name, False, float("nan"), float("nan"),
                              error=f"financials load failed: {exc}")
    bank = is_bank(ff)
    sources = ["edgar (primary)"]
    mcap = price = float("nan")
    if yfin is not None:
        mcap, price = _current_market_cap(yfin, ff, ticker)
        if mcap == mcap:
            sources.append("yfinance (non-primary)")

    prices = pd.DataFrame()
    if yfin is not None and fetch_prices:
        try:
            prices = yfin.get_prices(ticker, start="1970-01-01" if refresh else None)
        except Exception:  # noqa: BLE001
            prices = pd.DataFrame()

    current: dict = {}
    if mcap == mcap and mcap > 0:
        current["pe"] = _tail(pe_fy(ff, mcap))
        current["ev_ebitda"] = None if bank else _tail(ev_ebitda_fy(ff, mcap))
        current["fcf_yield"] = None if bank else _tail(fcf_yield_fy(ff, mcap))
        current["p_tbv"] = _tail(p_tbv_fy(ff, mcap)) if bank else None

    history = build_history(ff, prices, mcap, bank, ticker, share_basis) \
        if (mcap == mcap and mcap > 0) else None
    normalized = normalized_earnings(ff, price) if price == price else None
    dcf_cases = [] if bank else (_build_dcf(ff, mcap, price, wacc, terminal_growth, years)
                                 if (mcap == mcap and mcap > 0) else [])

    quality = {
        "roic": _tail(roic_fy(ff)),
        "fcf_margin": _tail(fcf_margin_fy(ff)),
        "net_debt_ebitda": None if bank else _tail(net_debt_to_ebitda_fy(ff)),
    }

    return TickerAnalysis(
        ticker=ticker, name=name, is_bank=bank, market_cap=mcap, price=price,
        current=current, quality=quality, history=history, normalized=normalized,
        dcf_cases=dcf_cases, sources=", ".join(sources),
    )


def _peer_table(subject: TickerAnalysis, peers: list[TickerAnalysis]) -> pd.DataFrame:
    """Subject first, peers, then PEER_MEDIAN / PEER_MEAN summary rows. Missing
    data renders NaN (never silent 0). `*_pctile_own_hist` is the discount-to-
    own-history headline per name; `pe_discount_to_peer_median` is the separate
    peer-relative lens — the two are kept distinct."""
    def row(a: TickerAnalysis) -> dict:
        return {
            "ticker": a.ticker,
            "name": a.name,
            "is_bank": a.is_bank,
            "market_cap": a.market_cap,
            "pe": a.current.get("pe", float("nan")),
            "ev_ebitda": a.current.get("ev_ebitda", float("nan")),
            "fcf_yield": a.current.get("fcf_yield", float("nan")),
            "p_tbv": a.current.get("p_tbv", float("nan")),
            "pe_pctile_own_hist": a.own_history_pctile("PE"),
            "ev_ebitda_pctile_own_hist": a.own_history_pctile("EV_EBITDA"),
            "fcf_yield_pctile_own_hist": a.own_history_pctile("FCF_Yield"),
            "p_tbv_pctile_own_hist": a.own_history_pctile("P_TBV"),
            "normalized_pe": a.normalized.normalized_pe if a.normalized else float("nan"),
            "roic": a.quality.get("roic", float("nan")),
            "fcf_margin": a.quality.get("fcf_margin", float("nan")),
            "net_debt_ebitda": a.quality.get("net_debt_ebitda", float("nan")),
            "implied_growth_base": a.base_implied_growth(),
            "margin_of_safety_base": a.base_margin_of_safety(),
            "sources": a.sources,
            "error": a.error or "",
        }

    df = pd.DataFrame([row(subject)] + [row(p) for p in peers]).set_index("ticker")
    peer_ids = [p.ticker for p in peers if p.error is None]
    if peer_ids:
        num = df.loc[peer_ids].select_dtypes(include=[np.number])
        med = num.median(numeric_only=True)
        mean = num.mean(numeric_only=True)
        # Peer-relative cheapness: subject's headline multiple vs the peer median.
        pe_med = med.get("pe", float("nan"))
        if pe_med and pe_med == pe_med and pe_med != 0:
            df["pe_discount_to_peer_median"] = df["pe"] / pe_med - 1.0
        else:
            df["pe_discount_to_peer_median"] = float("nan")
        for label, agg in (("PEER_MEDIAN", med), ("PEER_MEAN", mean)):
            df.loc[label] = {c: agg.get(c, np.nan) for c in df.columns}
            df.loc[label, "name"] = label
            df.loc[label, "sources"] = "derived (peer aggregate)"
            df.loc[label, "error"] = ""
    else:
        df["pe_discount_to_peer_median"] = float("nan")
    return df


@dataclass
class CompsResult:
    subject: TickerAnalysis
    peers: list[TickerAnalysis]
    peer_table: pd.DataFrame
    wacc: float
    terminal_growth: float
    forecast_years: int
    share_basis: str
    xlsx_path: Path | None = None
    md_path: Path | None = None


def run_comps(
    ticker: str,
    peers: list[str] | None = None,
    *,
    wacc: float | None = None,
    terminal_growth: float | None = None,
    forecast_years: int | None = None,
    share_basis: str = "wad",
    output_path: Path | str | None = None,
    fetch_prices: bool = True,
    refresh: bool = False,
) -> CompsResult:
    """Run the full comps + normalized-earnings + reverse-DCF pipeline for one
    subject against its peers and write the xlsx + markdown tear-sheet.

    Defaults for wacc / terminal_growth / forecast_years come from config.yaml
    (`valuation`); CLI flags override. Peers default to `config.peers[ticker]`.
    """
    cfg = load_config()
    ticker = ticker.upper()
    wacc = cfg.valuation.wacc if wacc is None else wacc
    terminal_growth = cfg.valuation.terminal_growth if terminal_growth is None else terminal_growth
    forecast_years = cfg.valuation.forecast_years if forecast_years is None else forecast_years
    if peers is None:
        peers = list(cfg.peers.get(ticker, []))
    peers = [p.upper() for p in peers if p.upper() != ticker]

    edgar = EdgarProvider()
    yfin = YFinanceProvider()

    kw = dict(wacc=wacc, terminal_growth=terminal_growth, years=forecast_years,
              share_basis=share_basis, fetch_prices=fetch_prices, refresh=refresh)
    subject = analyze(edgar, yfin, ticker, ticker, **kw)
    peer_analyses = [analyze(edgar, yfin, p, p, **kw) for p in peers]

    peer_table = _peer_table(subject, peer_analyses)

    out = Path(output_path) if output_path else (cache_dir() / f"comps_{ticker}.xlsx")
    md_out = out.with_suffix(".md")
    result = CompsResult(
        subject=subject, peers=peer_analyses, peer_table=peer_table,
        wacc=wacc, terminal_growth=terminal_growth, forecast_years=forecast_years,
        share_basis=share_basis, xlsx_path=out, md_path=md_out,
    )

    from .report import write_comps_md, write_comps_xlsx  # local import avoids cycle
    write_comps_xlsx(result, out)
    write_comps_md(result, md_out)
    return result

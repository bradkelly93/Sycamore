"""CSV-backed technical-screen overlay. NON-PRIMARY context only.

For technical signals TradingView's scanner API cannot expose — notably the
output of a CUSTOM Pine indicator (a regime/trend label such as "Trend
Chameleon"), which is computed in Pine and is not one of the scanner's built-in
fields. Drop a CSV in data/raw/ with a `ticker` column (optionally a
`passes_screen` column and any signal columns), and this returns the SAME tidy
frame the live API provider returns, so the screener overlay treats both
identically (membership join, carried `tv_` columns, divergence flags).

Produce the CSV however your indicator allows: TradingView's Pine Screener
(export), an alert log, or by hand from a watchlist. Presence in the file counts
as "passes the screen" unless a `passes_screen` column says otherwise.
"""

from __future__ import annotations

import pandas as pd

from .base import TechnicalScreenProvider
from ..config import load_config, raw_dir


SOURCE_TAG = "tradingview-csv (non-primary, technical)"

_TICKER_COLS = ("ticker", "symbol", "tickers")
_PASS_COLS = ("passes_screen", "passes", "pass", "member", "flag")
_TRUE = {"true", "1", "yes", "y", "t", "pass", "passes", "x"}


def _to_bool(v: object) -> bool:
    return str(v).strip().lower() in _TRUE


class CsvScreenProvider(TechnicalScreenProvider):
    name = "tradingview-csv"

    def get_screen(self, refresh: bool = False) -> pd.DataFrame:  # noqa: ARG002 — the file IS the cache
        cfg = load_config().tradingview
        path = raw_dir() / cfg.csv_file
        if not path.exists():
            raise FileNotFoundError(
                f"TradingView overlay CSV not found: {path}. Export your "
                "indicator's tickers there (a `ticker` column, optionally a "
                "`passes_screen` column and any signal columns), or set "
                "tradingview.mode: api in config.yaml."
            )

        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        raw.columns = [c.strip() for c in raw.columns]

        tcol = next((c for c in raw.columns if c.lower() in _TICKER_COLS), None)
        if tcol is None:
            raise ValueError(
                f"{path.name} needs a `ticker` column. Found: {list(raw.columns)}"
            )
        pcol = next((c for c in raw.columns if c.lower() in _PASS_COLS), None)
        scol = (
            next((c for c in raw.columns if c.lower() == cfg.signal_column.lower()), None)
            if cfg.signal_column else None
        )

        out = pd.DataFrame()
        # `EXCHANGE:SYMBOL` -> bare uppercase symbol (keep the class-share dot).
        out["ticker"] = raw[tcol].astype(str).str.split(":").str[-1].str.strip().str.upper()
        # Membership precedence: an explicit passes column > a regime/label column
        # mapped via tradingview.pass_values > presence in the file == passes.
        if pcol:
            out["passes_screen"] = raw[pcol].map(_to_bool)
        elif scol and cfg.pass_values:
            keep = {str(v).strip().lower() for v in cfg.pass_values}
            out["passes_screen"] = raw[scol].astype(str).str.strip().str.lower().isin(keep)
        else:
            out["passes_screen"] = True
        # Carry every other column through, coercing to numeric where possible.
        for c in raw.columns:
            if c == tcol or c == pcol:
                continue
            num = pd.to_numeric(raw[c], errors="coerce")
            out[c] = num.where(num.notna(), raw[c])

        out = out[out["ticker"].str.len().between(1, 12)].reset_index(drop=True)
        out["asof"] = pd.Timestamp.fromtimestamp(path.stat().st_mtime, tz="UTC").isoformat()
        out["source"] = SOURCE_TAG
        return out

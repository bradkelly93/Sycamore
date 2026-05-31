"""Credential / config status for the sidebar — presence only, never values.

Mirrors the CLI's env discipline: SEC user-agent comes from config.yaml;
tastytrade (OAuth2) and the optional TradingView session id come from env. We
report ✓/✗ and the NAMES of any missing keys — never the secret values.
"""

from __future__ import annotations

import os

from ..adapters import TastytradeProvider
from ..config import load_config
from .viewmodels import CredsStatus


def status() -> CredsStatus:
    cfg = load_config()
    ua = (getattr(cfg.edgar, "user_agent", "") or "").strip()

    secret = os.environ.get("TASTYTRADE_CLIENT_SECRET") or os.environ.get("TT_SECRET")
    refresh_tok = os.environ.get("TASTYTRADE_REFRESH_TOKEN") or os.environ.get("TT_REFRESH")
    missing: list[str] = []
    if not secret:
        missing.append("TASTYTRADE_CLIENT_SECRET")
    if not refresh_tok:
        missing.append("TASTYTRADE_REFRESH_TOKEN")

    tv = cfg.tradingview
    tv_sessionid = bool(os.environ.get("TV_SESSIONID") or getattr(tv, "sessionid", None))

    return CredsStatus(
        sec_user_agent={"detected": bool(ua), "origin": "config.yaml"},
        tastytrade={"detected": TastytradeProvider.available(), "missing": missing},
        tradingview={"sessionid_detected": tv_sessionid, "enabled": tv.enabled, "mode": tv.mode},
        prediction={"enabled": True},  # Polymarket needs no creds
        valuation_defaults={
            "wacc": cfg.valuation.wacc,
            "terminal_growth": cfg.valuation.terminal_growth,
            "forecast_years": cfg.valuation.forecast_years,
        },
        peers={k: list(v) for k, v in cfg.peers.items()},
    )


def refresh() -> None:
    """Drop the ``@lru_cache`` on ``load_config`` so a config.yaml edit is seen."""
    load_config.cache_clear()

"""yfinance sector resolution — normalizes yfinance's sector vocabulary to the
GICS labels config.yaml + the universe builder use. No network (get_info is
monkeypatched)."""

from __future__ import annotations

import pytest

from sycamore_prep.adapters.yfinance_provider import YFinanceProvider


def _provider_returning(info: object, monkeypatch) -> YFinanceProvider:
    prov = YFinanceProvider()
    # get_sector pulls from yf.Ticker(t).get_info() via self._retry; stub _retry
    # so we never import/hit yfinance.
    monkeypatch.setattr(prov, "_retry", lambda fn, *a, **k: info)
    return prov


@pytest.mark.parametrize(
    "yf_sector,expected_gics",
    [
        ("Financial Services", "Financials"),     # the UMBF case
        ("Healthcare", "Health Care"),
        ("Technology", "Information Technology"),
        ("Consumer Cyclical", "Consumer Discretionary"),
        ("Consumer Defensive", "Consumer Staples"),
        ("Basic Materials", "Materials"),
        ("Energy", "Energy"),                     # already GICS — passthrough
        ("Industrials", "Industrials"),
    ],
)
def test_get_sector_normalizes_to_gics(yf_sector, expected_gics, monkeypatch):
    prov = _provider_returning({"sector": yf_sector}, monkeypatch)
    assert prov.get_sector("XYZ") == expected_gics


def test_get_sector_none_when_missing(monkeypatch):
    assert _provider_returning({}, monkeypatch).get_sector("XYZ") is None
    assert _provider_returning({"sector": ""}, monkeypatch).get_sector("XYZ") is None


def test_get_sector_none_on_lookup_failure(monkeypatch):
    prov = YFinanceProvider()

    def boom(fn, *a, **k):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(prov, "_retry", boom)
    assert prov.get_sector("XYZ") is None  # degrades gracefully, no raise

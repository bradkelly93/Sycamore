"""yfinance sector resolution — normalizes yfinance's sector vocabulary to the
GICS labels config.yaml + the universe builder use. No network (get_info is
monkeypatched)."""

from __future__ import annotations

import pytest

from sycamore_prep.adapters import cache as cache_mod
from sycamore_prep.adapters.yfinance_provider import YFinanceProvider


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point the sector cache at a fresh tmp dir for every test so cached
    lookups don't leak across cases (or into the real data/cache/)."""
    monkeypatch.setattr(cache_mod, "cache_dir", lambda: tmp_path)


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


def test_get_sector_caches_success_and_avoids_second_lookup(tmp_path, monkeypatch):
    prov = YFinanceProvider()
    calls = {"n": 0}

    def counting_retry(fn, *a, **k):
        calls["n"] += 1
        return {"sector": "Financial Services"}

    monkeypatch.setattr(prov, "_retry", counting_retry)
    assert prov.get_sector("UMBF") == "Financials"   # normalized + cached
    assert prov.get_sector("UMBF") == "Financials"   # served from cache
    assert calls["n"] == 1                            # no second yfinance call
    assert cache_mod.load_sector("UMBF") == "Financials"


def test_get_sector_does_not_cache_failures(tmp_path, monkeypatch):
    prov = YFinanceProvider()

    def boom(fn, *a, **k):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(prov, "_retry", boom)
    assert prov.get_sector("XYZ") is None
    # A transient outage must not poison the cache — nothing persisted, so the
    # next run retries.
    assert cache_mod.load_sector("XYZ") is None
    assert not cache_mod.sector_path("XYZ").exists()


def test_get_sector_none_when_yfinance_unimportable(monkeypatch):
    """An uncached ticker with yfinance fully unavailable returns None, not a
    ModuleNotFoundError — the import is inside the guard."""
    import sycamore_prep.adapters.yfinance_provider as yfp

    def no_yf():
        raise ModuleNotFoundError("No module named 'yfinance'")

    monkeypatch.setattr(yfp, "_yf", no_yf)
    assert YFinanceProvider().get_sector("UNCACHED") is None


def test_get_sector_use_cache_false_bypasses_cache(tmp_path, monkeypatch):
    cache_mod.save_sector("WES", "StaleValue")
    prov = YFinanceProvider()
    monkeypatch.setattr(prov, "_retry", lambda fn, *a, **k: {"sector": "Energy"})
    # use_cache=False ignores the stale entry, re-resolves, and refreshes it.
    assert prov.get_sector("WES", use_cache=False) == "Energy"
    assert cache_mod.load_sector("WES") == "Energy"

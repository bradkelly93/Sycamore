"""TradingView overlay adapter tests — fully offline.

The `tradingview-screener` library is never imported: `_tv()` is monkeypatched
to a fake Query/col, and the cache is stubbed. Covers symbol normalization, the
declarative filter→query compiler, tagging/normalization on pull, the truncation
guard, and the TTL cache short-circuit.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sycamore_prep.adapters import tradingview as tv_mod
from sycamore_prep.adapters.tradingview import (
    SOURCE_TAG,
    TradingViewProvider,
    _compile_filters,
    _normalize_symbol,
)


class _FakeCol:
    """Records the operator + operand instead of building a real condition."""

    def __init__(self, field: str):
        self.field = field

    def __gt__(self, other):  return ("gt", self.field, other)
    def __lt__(self, other):  return ("lt", self.field, other)
    def __ge__(self, other):  return ("ge", self.field, other)
    def __le__(self, other):  return ("le", self.field, other)
    def __eq__(self, other):  return ("eq", self.field, other)  # noqa: PLE0643
    def __hash__(self):       return id(self)
    def between(self, a, b):  return ("between", self.field, (a, b))
    def isin(self, v):        return ("isin", self.field, list(v))
    def crosses_above(self, o): return ("crosses_above", self.field, o)
    def crosses_below(self, o): return ("crosses_below", self.field, o)


def _fake_col(field: str) -> _FakeCol:
    return _FakeCol(field)


def _make_fake_tv(result):
    """Return a fake `_tv()` yielding (QueryClass, col) where the query is a
    chainable no-op whose get_scanner_data returns `result` (count, df)."""

    class _Q:
        def set_markets(self, *a):  return self
        def select(self, *a):       return self
        def where(self, *a):        return self
        def limit(self, n):         return self
        def get_scanner_data(self, cookies=None):  return result

    return lambda: (_Q, _fake_col)


def test_normalize_symbol():
    assert _normalize_symbol("NASDAQ:AAPL") == "AAPL"
    assert _normalize_symbol("NYSE:MOG.A") == "MOG.A"   # keep the class-share dot
    assert _normalize_symbol("NYSE:BRK.B") == "BRK.B"
    assert _normalize_symbol("aapl") == "AAPL"


def test_compile_filters_op_map():
    filters = [
        {"field": "RSI", "op": "lt", "value": 70},
        {"field": "close", "op": "above", "value": "SMA200"},
        {"field": "market_cap_basic", "op": "between", "value": [1, 2]},
        {"field": "exchange", "op": "isin", "value": ["NYSE", "NASDAQ"]},
        {"field": "x", "op": "crosses_above", "value": "y"},
    ]
    conds = _compile_filters(filters, _fake_col)

    assert conds[0] == ("lt", "RSI", 70)              # literal
    op, fld, val = conds[1]                            # field-vs-field
    assert (op, fld) == ("gt", "close")
    assert isinstance(val, _FakeCol) and val.field == "SMA200"
    assert conds[2] == ("between", "market_cap_basic", (1, 2))
    assert conds[3] == ("isin", "exchange", ["NYSE", "NASDAQ"])
    op, fld, val = conds[4]
    assert (op, fld) == ("crosses_above", "x")
    assert isinstance(val, _FakeCol) and val.field == "y"


def test_compile_filters_rejects_unknown_op():
    with pytest.raises(ValueError):
        _compile_filters([{"field": "RSI", "op": "wat", "value": 1}], _fake_col)


def test_get_screen_normalizes_and_tags(monkeypatch):
    df = pd.DataFrame({
        "ticker": ["NASDAQ:AAA", "NYSE:MOG.A"],
        "RSI": [55.0, 48.0],
        "close": [10.0, 20.0],
    })
    monkeypatch.setattr(tv_mod, "_tv", _make_fake_tv((2, df)))
    monkeypatch.setattr(tv_mod.cache, "load_tv_screen", lambda ttl: None)
    saved: dict = {}
    monkeypatch.setattr(tv_mod.cache, "save_tv_screen", lambda d: saved.update(df=d))

    out = TradingViewProvider().get_screen()

    assert list(out["ticker"]) == ["AAA", "MOG.A"]   # exchange prefix stripped
    assert out["passes_screen"].all()
    assert (out["source"] == SOURCE_TAG).all()
    assert out["asof"].notna().all()
    assert "df" in saved                              # snapshot cached


def test_get_screen_truncation_warns(monkeypatch):
    df = pd.DataFrame({"ticker": ["NASDAQ:AAA"], "RSI": [55.0]})
    monkeypatch.setattr(tv_mod, "_tv", _make_fake_tv((99999, df)))
    monkeypatch.setattr(tv_mod.cache, "load_tv_screen", lambda ttl: None)
    monkeypatch.setattr(tv_mod.cache, "save_tv_screen", lambda d: None)

    out = TradingViewProvider().get_screen()
    assert out.attrs.get("tv_truncated") is True


def test_get_screen_uses_cache(monkeypatch):
    cached = pd.DataFrame({
        "ticker": ["AAA"], "passes_screen": [True],
        "asof": ["2026-05-30T00:00:00+00:00"], "source": [SOURCE_TAG],
    })
    monkeypatch.setattr(tv_mod.cache, "load_tv_screen", lambda ttl: cached)

    def _boom():
        raise AssertionError("_tv() must not be called on a cache hit")

    monkeypatch.setattr(tv_mod, "_tv", _boom)
    out = TradingViewProvider().get_screen()   # refresh=False → cache hit
    assert list(out["ticker"]) == ["AAA"]

"""Unit tests for yfinance adapter helpers. No network."""

from __future__ import annotations

from sycamore_prep.adapters.yfinance_provider import _yf_symbol


def test_yf_symbol_translates_share_classes_to_dash():
    # yfinance uses a dash for share classes; SEC/config use the dot form.
    assert _yf_symbol("MOG.A") == "MOG-A"
    assert _yf_symbol("BRK.B") == "BRK-B"
    assert _yf_symbol("CW") == "CW"
    assert _yf_symbol("caci") == "CACI"

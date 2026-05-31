"""Service Universe view surfaces the Sycamore fund holdings."""

from __future__ import annotations

import json

from sycamore_prep.service import universe as svc
from sycamore_prep.service.viewmodels import Figure


def test_status_surfaces_sycamore_holdings(wl_root, monkeypatch):
    # Force "not built" deterministically (load_universe reads the builder's real
    # cache, which wl_root doesn't redirect); the committed Sycamore holdings must
    # still populate the overlay section regardless.
    monkeypatch.setattr(svc, "load_universe", lambda: None)
    v = svc.status()
    assert v.built is False
    assert v.sycamore_funds.get("Established Value Fund") == 72
    assert v.sycamore_funds.get("Small Company Opportunity Fund") == 104
    assert len(v.sycamore_holdings) == 176
    row = next(h for h in v.sycamore_holdings if h.ticker == "LH")
    assert isinstance(row.weight_pct, Figure)
    assert row.weight_pct.primary is False          # disclosed holdings, non-primary
    assert row.weight_pct.value is not None
    # JSON-safe across the whole view.
    json.loads(v.model_dump_json())


def test_sycamore_sectors_present(wl_root):
    v = svc.status()
    assert "Industrials" in v.sycamore_sectors
    assert sum(v.sycamore_sectors.values()) == 176

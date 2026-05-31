"""CredsStatus: presence only, NEVER values."""

from __future__ import annotations

from sycamore_prep.service import creds


def test_status_reports_presence_not_values(monkeypatch):
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "SUPERSECRET_VALUE_123")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "REFRESH_VALUE_456")
    s = creds.status()
    assert s.tastytrade["detected"] is True
    assert s.tastytrade["missing"] == []
    assert s.sec_user_agent["origin"] == "config.yaml"
    assert "wacc" in s.valuation_defaults
    # CRITICAL: no secret value leaks anywhere in the serialized status.
    dump = s.model_dump_json()
    assert "SUPERSECRET_VALUE_123" not in dump
    assert "REFRESH_VALUE_456" not in dump


def test_status_reports_missing_creds(monkeypatch):
    for k in ("TASTYTRADE_CLIENT_SECRET", "TT_SECRET",
              "TASTYTRADE_REFRESH_TOKEN", "TT_REFRESH"):
        monkeypatch.delenv(k, raising=False)
    s = creds.status()
    assert s.tastytrade["detected"] is False
    assert "TASTYTRADE_CLIENT_SECRET" in s.tastytrade["missing"]
    assert "TASTYTRADE_REFRESH_TOKEN" in s.tastytrade["missing"]

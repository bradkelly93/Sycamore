"""Put-skew streaming tests — all offline.

Every pure piece (chain parse, expiration selection, dxLink message builders,
FEED_CONFIG/FEED_DATA parsers, skew calc) is unit-tested, and the streaming
orchestration is driven against a scripted fake websocket so the only
unverifiable surface is the raw socket I/O itself.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from sycamore_prep.adapters.tastytrade_provider import TastytradeProvider
from sycamore_prep.adapters.tastytrade_stream import (
    DXLINK_VERSION,
    auth_msg,
    channel_request_msg,
    feed_setup_msg,
    feed_subscription_msg,
    parse_feed_config_fields,
    parse_greeks_feed_data,
    parse_nested_option_chain,
    parse_quote_token,
    select_chain_expiration,
    setup_msg,
    skew_from_greeks,
    stream_greeks,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
ASOF = "2026-05-30"


def _chain_payload() -> dict:
    return json.loads((FIXTURE_DIR / "option_chain_nested_min.json").read_text())


# --------------------------------------------------------------------------
# REST payload parsers
# --------------------------------------------------------------------------
def test_parse_quote_token():
    url, token = parse_quote_token({"data": {"dxlink-url": "wss://x", "token": "tok"}})
    assert url == "wss://x"
    assert token == "tok"


def test_parse_nested_option_chain():
    chain = parse_nested_option_chain(_chain_payload())
    assert [e["expiration_date"] for e in chain] == ["2026-06-19", "2026-08-21"]
    first = chain[0]["options"]
    assert first[0]["call_streamer_symbol"] == ".CW260619C95"
    assert first[0]["put_streamer_symbol"] == ".CW260619P95"
    assert first[0]["strike"] == pytest.approx(95.0)


def test_select_chain_expiration_modes():
    chain = parse_nested_option_chain(_chain_payload())
    # nearest non-expired
    assert select_chain_expiration(chain, asof=ASOF)["expiration_date"] == "2026-06-19"
    # nearest to a target horizon (~80d) -> the August expiration
    assert select_chain_expiration(chain, target_days=80, asof=ASOF)["expiration_date"] == "2026-08-21"
    # first expiration on/after earnings (2026-07-30) -> August
    assert (
        select_chain_expiration(chain, on_or_after="2026-07-30", asof=ASOF)["expiration_date"]
        == "2026-08-21"
    )


# --------------------------------------------------------------------------
# dxLink message builders + feed parsers
# --------------------------------------------------------------------------
def test_dxlink_message_builders():
    s = setup_msg()
    assert s["version"] == DXLINK_VERSION and s["channel"] == 0
    assert auth_msg("t")["token"] == "t"
    cr = channel_request_msg()
    assert cr["service"] == "FEED" and cr["parameters"]["contract"] == "AUTO"
    fs = feed_setup_msg()
    assert fs["acceptDataFormat"] == "COMPACT" and "Greeks" in fs["acceptEventFields"]
    sub = feed_subscription_msg([".A", ".B"])
    assert [a["symbol"] for a in sub["add"]] == [".A", ".B"]
    assert all(a["type"] == "Greeks" for a in sub["add"])


def test_parse_feed_config_fields():
    msg = {"type": "FEED_CONFIG", "eventFields": {"Greeks": ["eventSymbol", "volatility", "delta"]}}
    assert parse_feed_config_fields(msg) == ["eventSymbol", "volatility", "delta"]
    assert parse_feed_config_fields({"type": "FEED_CONFIG"}) is None


def test_parse_greeks_feed_data_compact_chunking():
    msg = {
        "type": "FEED_DATA",
        "data": ["Greeks", [".CW260821P90", 0.40, -0.25, ".CW260821C110", 0.30, 0.25]],
    }
    out = parse_greeks_feed_data(msg, ["eventSymbol", "volatility", "delta"])
    assert out[".CW260821P90"]["volatility"] == pytest.approx(0.40)
    assert out[".CW260821P90"]["delta"] == pytest.approx(-0.25)
    assert out[".CW260821C110"]["delta"] == pytest.approx(0.25)


def test_parse_greeks_feed_data_respects_server_field_order():
    # Server declared a different order in FEED_CONFIG; parser must honor it.
    msg = {"type": "FEED_DATA", "data": ["Greeks", [".X", -0.30, 0.45]]}
    out = parse_greeks_feed_data(msg, ["eventSymbol", "delta", "volatility"])
    assert out[".X"]["delta"] == pytest.approx(-0.30)
    assert out[".X"]["volatility"] == pytest.approx(0.45)


def test_skew_from_greeks():
    greeks = {
        ".P90": {"delta": -0.25, "volatility": 0.40},   # 25d put
        ".C110": {"delta": 0.25, "volatility": 0.30},   # 25d call
        ".C100": {"delta": 0.55, "volatility": 0.28},   # ATM-ish call (ignored)
    }
    res = skew_from_greeks(greeks, expiration_date="2026-08-21")
    assert res["put_skew_25d"] == pytest.approx(0.10)   # 0.40 - 0.30
    assert res["expiration_date"] == "2026-08-21"
    assert res["n_strikes"] == 3


# --------------------------------------------------------------------------
# Streaming orchestration vs. a scripted fake websocket
# --------------------------------------------------------------------------
def test_stream_greeks_runs_handshake_and_collects(monkeypatch):
    server_script = [
        {"type": "SETUP", "channel": 0},
        {"type": "AUTH_STATE", "state": "AUTHORIZED"},
        {"type": "CHANNEL_OPENED", "channel": 1},
        {"type": "FEED_CONFIG", "channel": 1, "eventFields": {"Greeks": ["eventSymbol", "volatility", "delta"]}},
        {"type": "FEED_DATA", "channel": 1, "data": ["Greeks", [".CW260821P90", 0.40, -0.25, ".CW260821C110", 0.30, 0.25]]},
    ]

    class FakeWS:
        def __init__(self, script):
            self._script = list(script)
            self.sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, data):
            self.sent.append(json.loads(data))

        async def recv(self):
            assert self._script, "recv called after script exhausted"
            return json.dumps(self._script.pop(0))

    fake_ws = FakeWS(server_script)
    fake_mod = types.SimpleNamespace(connect=lambda url, **kw: fake_ws)
    monkeypatch.setitem(sys.modules, "websockets", fake_mod)

    result = asyncio.run(
        stream_greeks("wss://x", "tok", [".CW260821P90", ".CW260821C110"], timeout=5)
    )

    assert result[".CW260821P90"]["delta"] == pytest.approx(-0.25)
    assert result[".CW260821C110"]["volatility"] == pytest.approx(0.30)

    sent_types = [m["type"] for m in fake_ws.sent]
    assert sent_types == ["SETUP", "AUTH", "CHANNEL_REQUEST", "FEED_SETUP", "FEED_SUBSCRIPTION"]
    assert next(m for m in fake_ws.sent if m["type"] == "AUTH")["token"] == "tok"
    sub = next(m for m in fake_ws.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert {s["symbol"] for s in sub["add"]} == {".CW260821P90", ".CW260821C110"}


def test_provider_put_skew_orchestration(monkeypatch):
    chain_payload = _chain_payload()

    def fake_get(self, path, params=None):  # noqa: ARG001
        assert "/option-chains/CW/nested" in path
        return chain_payload

    def fake_greeks(self, symbols, timeout=20.0):  # noqa: ARG001
        # The August expiration's 25-delta wings.
        return {
            ".CW260821P90": {"delta": -0.25, "volatility": 0.40},
            ".CW260821C110": {"delta": 0.25, "volatility": 0.30},
        }

    monkeypatch.setattr(TastytradeProvider, "_get", fake_get)
    monkeypatch.setattr(TastytradeProvider, "get_option_greeks", fake_greeks)

    prov = TastytradeProvider(client_secret="s", refresh_token="r", base_url="https://x")
    res = prov.put_skew("CW", target_days=80, asof=ASOF)
    assert res["expiration_date"] == "2026-08-21"
    assert res["put_skew_25d"] == pytest.approx(0.10)

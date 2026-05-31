"""tastytrade dxLink streaming — per-strike option Greeks for put skew.

Put skew (25-delta put IV minus 25-delta call IV) needs implied vol + delta at
specific strikes. tastytrade exposes that ONLY over the dxLink websocket feed
(the REST `market-data` endpoint has no Greeks), so this module speaks the
dxLink protocol to pull a one-shot Greeks snapshot for an expiration's chain.

Design for testability: everything here is a PURE function except
`stream_greeks`, which is a thin async orchestration of those pure pieces.
`stream_greeks` itself is exercised in tests against a scripted fake websocket,
so the only unverifiable surface is the raw socket I/O. The protocol mirrors
the tastytrade SDK's known-working message sequence:

    SETUP -> (server SETUP) -> AUTH -> AUTH_STATE(AUTHORIZED)
          -> CHANNEL_REQUEST(FEED) -> CHANNEL_OPENED
          -> FEED_SETUP -> FEED_CONFIG(echoes field order)
          -> FEED_SUBSCRIPTION -> FEED_DATA(Greeks)...

The `websockets` package is an optional dependency (install `sycamore-prep[vol]`)
and is imported lazily so the rest of the toolkit runs without it.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

# dxLink protocol version string the tastytrade streamer expects.
DXLINK_VERSION = "0.1-DXF-JS/0.3.0"
GREEKS_CHANNEL = 1
# Minimal Greeks fields we need; the server echoes the actual order in
# FEED_CONFIG, which we honor when parsing (so order here is only a request).
GREEKS_FIELDS = ["eventSymbol", "volatility", "delta"]


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# REST payload parsers (pure)
# ---------------------------------------------------------------------------
def parse_quote_token(payload: dict) -> tuple[str | None, str | None]:
    """(/api-quote-tokens) -> (dxlink_url, token)."""
    d = (payload or {}).get("data") or {}
    return d.get("dxlink-url"), d.get("token")


def parse_nested_option_chain(payload: dict) -> list[dict]:
    """(/option-chains/{symbol}/nested) -> list of expirations.

    Each expiration: {"expiration_date": "YYYY-MM-DD", "options": [
        {"strike": float, "call_streamer_symbol": str, "put_streamer_symbol": str}, ...
    ]}.
    """
    items = ((payload or {}).get("data") or {}).get("items") or []
    out: list[dict] = []
    for underlying in items:
        for exp in underlying.get("expirations") or []:
            edate = exp.get("expiration-date")
            if not edate:
                continue
            options = [
                {
                    "strike": _to_float(s.get("strike-price")),
                    "call_streamer_symbol": s.get("call-streamer-symbol"),
                    "put_streamer_symbol": s.get("put-streamer-symbol"),
                }
                for s in (exp.get("strikes") or [])
            ]
            out.append({"expiration_date": str(edate), "options": options})
    return out


def select_chain_expiration(
    expirations: list[dict],
    on_or_after=None,
    target_days: float | None = None,
    asof=None,
) -> dict | None:
    """Pick one expiration from a parsed nested chain.

    Priority: first expiration on/after `on_or_after` (e.g. next earnings), else
    nearest to `target_days` out, else the nearest non-expired expiration.
    """
    base = _coerce_date(asof) or date.today()
    cand: list[tuple[int, date, dict]] = []
    for e in expirations:
        d = _coerce_date(e.get("expiration_date"))
        if d is None:
            continue
        dte = (d - base).days
        if dte < 0:
            continue
        cand.append((dte, d, e))
    if not cand:
        return None
    oa = _coerce_date(on_or_after)
    if oa is not None:
        after = [c for c in cand if c[1] >= oa]
        if after:
            return min(after, key=lambda c: c[0])[2]
    if target_days is not None:
        return min(cand, key=lambda c: abs(c[0] - target_days))[2]
    return min(cand, key=lambda c: c[0])[2]


# ---------------------------------------------------------------------------
# dxLink message builders (pure)
# ---------------------------------------------------------------------------
def setup_msg() -> dict:
    return {
        "type": "SETUP",
        "channel": 0,
        "keepaliveTimeout": 60,
        "acceptKeepaliveTimeout": 60,
        "version": DXLINK_VERSION,
    }


def auth_msg(token: str) -> dict:
    return {"type": "AUTH", "channel": 0, "token": token}


def channel_request_msg(channel: int = GREEKS_CHANNEL) -> dict:
    return {
        "type": "CHANNEL_REQUEST",
        "channel": channel,
        "service": "FEED",
        "parameters": {"contract": "AUTO"},
    }


def feed_setup_msg(channel: int = GREEKS_CHANNEL, fields: list[str] | None = None) -> dict:
    return {
        "type": "FEED_SETUP",
        "channel": channel,
        "acceptAggregationPeriod": 10,
        "acceptDataFormat": "COMPACT",
        "acceptEventFields": {"Greeks": list(fields or GREEKS_FIELDS)},
    }


def feed_subscription_msg(symbols: Iterable[str], channel: int = GREEKS_CHANNEL) -> dict:
    return {
        "type": "FEED_SUBSCRIPTION",
        "channel": channel,
        "add": [{"type": "Greeks", "symbol": s} for s in symbols],
    }


# ---------------------------------------------------------------------------
# Feed parsers (pure)
# ---------------------------------------------------------------------------
def parse_feed_config_fields(message: dict, event_type: str = "Greeks") -> list[str] | None:
    """FEED_CONFIG -> the authoritative field order the server will send."""
    fields = ((message or {}).get("eventFields") or {}).get(event_type)
    return list(fields) if fields else None


def parse_greeks_feed_data(message: dict, fields: list[str]) -> dict:
    """COMPACT FEED_DATA -> {streamer_symbol: {"volatility": float, "delta": float}}.

    COMPACT data is ["Greeks", [v0, v1, ... repeated per event]]; `fields` gives
    the value order (from FEED_CONFIG). Robust to field order via index lookup.
    """
    data = (message or {}).get("data") or []
    if len(data) < 2 or not fields:
        return {}
    flat = data[1]
    if not isinstance(flat, list):
        return {}
    try:
        sym_i = fields.index("eventSymbol")
        vol_i = fields.index("volatility")
        del_i = fields.index("delta")
    except ValueError:
        return {}
    n = len(fields)
    out: dict = {}
    for k in range(0, len(flat) - n + 1, n):
        chunk = flat[k : k + n]
        sym = chunk[sym_i]
        if not isinstance(sym, str):
            continue
        out[sym] = {"volatility": _to_float(chunk[vol_i]), "delta": _to_float(chunk[del_i])}
    return out


def skew_from_greeks(greeks_by_symbol: dict, wing: float = 0.25, expiration_date=None) -> dict | None:
    """Compute 25-delta put skew from a Greeks snapshot.

    dxFeed delta is already signed (puts negative, calls positive), so we map
    delta -> IV across all options and let put_skew pick the wings.
    """
    delta_iv: dict[float, float] = {}
    for g in greeks_by_symbol.values():
        d = _to_float(g.get("delta"))
        iv = _to_float(g.get("volatility"))
        if d is None or iv is None:
            continue
        delta_iv[d] = iv
    from ..metrics.volatility import put_skew  # lazy: avoid an import cycle

    skew = put_skew(delta_iv, wing=wing)
    if skew is None:
        return None
    return {
        "put_skew_25d": skew,
        "wing": wing,
        "expiration_date": expiration_date,
        "n_strikes": len(delta_iv),
    }


# ---------------------------------------------------------------------------
# Streaming orchestration (the only non-pure piece; tested via a fake socket)
# ---------------------------------------------------------------------------
async def stream_greeks(
    url: str,
    token: str,
    symbols: Iterable[str],
    timeout: float = 20.0,
    channel: int = GREEKS_CHANNEL,
) -> dict:
    """Connect, run the dxLink handshake, subscribe Greeks, and collect a
    one-shot snapshot {symbol: {"volatility", "delta"}} until every requested
    symbol is seen or `timeout` elapses."""
    try:
        import websockets  # optional dep; lazy
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "Put-skew streaming needs the 'websockets' package. "
            "Install it with: pip install 'sycamore-prep[vol]'"
        ) from exc
    import asyncio
    import json
    import time

    want = list(dict.fromkeys(s for s in symbols if s))
    want_set = set(want)
    fields = list(GREEKS_FIELDS)
    collected: dict = {}
    deadline = time.monotonic() + timeout

    # ping_interval=None: tastytrade uses its own KEEPALIVE, not ws pings.
    async with websockets.connect(
        url, ping_interval=None, open_timeout=min(timeout, 10), max_size=None
    ) as ws:
        await ws.send(json.dumps(setup_msg()))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "SETUP":
                await ws.send(json.dumps(auth_msg(token)))
            elif mtype == "AUTH_STATE":
                if msg.get("state") == "AUTHORIZED":
                    await ws.send(json.dumps(channel_request_msg(channel)))
            elif mtype == "CHANNEL_OPENED":
                await ws.send(json.dumps(feed_setup_msg(channel, fields)))
            elif mtype == "FEED_CONFIG":
                got = parse_feed_config_fields(msg)
                if got:
                    fields = got
                await ws.send(json.dumps(feed_subscription_msg(want, channel)))
            elif mtype == "FEED_DATA":
                for sym, g in parse_greeks_feed_data(msg, fields).items():
                    if g.get("delta") is not None and g.get("volatility") is not None:
                        collected[sym] = g
                if want_set and want_set.issubset(collected.keys()):
                    break
            elif mtype == "ERROR":
                raise RuntimeError(f"dxlink error: {msg.get('message')}")
    return collected

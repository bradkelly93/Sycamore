# Sycamore Interview Prep Toolkit

Decision-support tooling for preparing a bottom-up value pitch and modeling
exercise for a Research Analyst interview at Sycamore Capital. Read
`CLAUDE.md` for the operating philosophy before changing anything.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Then edit `config.yaml` and set a real contact string in `edgar.user_agent`
— the SEC rejects requests without one.

## CLI

The toolkit ships a single `sycamore-prep` command (typer). Run
`sycamore-prep --help` to see what is wired up.

### Phase 1 — Data + Universe

Pull and cache primary-source fundamentals (SEC EDGAR XBRL) for a ticker:

```bash
sycamore-prep pull-fundamentals CW
sycamore-prep pull-fundamentals CW WES UMBF LECO MTDR
```

Inspect what is cached:

```bash
sycamore-prep show-financials CW --concept Revenues
```

Build the investable universe (IWS + IWN + Sycamore overlay). Drop the
holdings CSVs in `data/raw/` first:

- `data/raw/iws_holdings.csv` — iShares Russell Mid-Cap Value
- `data/raw/iwn_holdings.csv` — iShares Russell 2000 Value
- `data/raw/sycamore_holdings.csv` — Sycamore fund holdings overlay (optional)

```bash
sycamore-prep build-universe
```

Outputs:

- `data/cache/universe.parquet`
- `data/cache/universe.csv` (human-readable)

The universe table includes `in_iws`, `in_iwn`, `owned_by_sycamore` flags and
a `source` column on every row.

## Project layout

See `CLAUDE.md` for the repo map and operating principles.

### Phase 2 — Screener

Run the three-attribute quality-value screen. By default it pulls market
caps from yfinance:

```bash
sycamore-prep screen CW WES UMBF LECO MTDR
sycamore-prep screen --sector banks --limit 50
sycamore-prep screen --skip-market-cap                 # quality + improving only
sycamore-prep screen --hard-exclude-negative-space     # drop flagged names from the rank
```

Output (`data/cache/screener_output.xlsx`) reports — **for every name** —
the three sub-scores **separately** (Q1 Quality, Q2 Valuation, Q3 Improving
Fundamentals) plus the raw components and the negative-space flags. The
composite rank is the sort key only; per `CLAUDE.md` the toolkit never
collapses the three attributes into one opaque number.

**Negative-space handling.** Names that trip a Sycamore-style red flag
(no/low FCF, extreme P/E, extreme EV/EBITDA, high leverage for non-banks)
are **kept in the output by default** with all three sub-scores visible —
just flagged in the `ns_flags` column and sorted to the bottom. This keeps
the three-axis decomposition intact (you can still see a great business that
happens to be priced rich). Pass `--hard-exclude-negative-space` to remove
them from the ranking entirely.

EBITDA is computed as EBIT + D&A from the cash-flow add-back. **If you
pulled fundamentals before this was added, re-pull with
`pull-fundamentals --refresh` so the D&A line populates** — otherwise
EV/EBITDA and net-debt/EBITDA fall back to an EBIT-only proxy that
over-states leverage for D&A-heavy businesses (midstream, industrials).

#### TradingView technical overlay (non-primary context)

You can overlay your **own saved TradingView technical screen** onto the
fundamental output. This is an opt-in, **non-primary context** layer — by
design it **never enters the Q1/Q2/Q3 sub-scores, the composite, or the rank**
(per `CLAUDE.md`: bottom-up only, downside-first). It is added strictly to the
*right* of the downside (`negative_space`/`ns_flags`) columns so margin-of-safety
stays the most prominent read.

Transcribe your screen's conditions into the `tradingview:` block in
`config.yaml` (declarative `{field, op, value}` filters, AND-combined), then:

```bash
sycamore-prep screen CW WES UMBF LECO MTDR --tv-overlay
sycamore-prep screen --sector industrials --tv-overlay --refresh-tv   # force re-pull
```

The overlay adds, per name: `passes_screen` (is the ticker in your screen),
the carried indicators (`tv_close`, `tv_RSI`, ...), `tv_asof` (snapshot time —
technicals go stale fast), and a neutral **`tv_divergence`** flag derived
*after the fact* from the **pure** composite crossed with screen membership:

| `tv_divergence` | meaning | downside-first reading |
|---|---|---|
| `agree_strong` | strong fundamentals **and** passes the screen | thesis and tape agree |
| `agree_weak` | weak fundamentals **and** fails the screen | neither likes it |
| `diverge_fund_strong_tech_fail` | strong fundamentals, fails the screen | **research prompt, not a signal** — ask what the tape may be pricing that the filings haven't shown yet; re-examine the bear case. Score is unchanged. |
| `diverge_fund_weak_tech_pass` | weak fundamentals, passes the screen | **do-not-chase prompt** — technical strength is not a reason to override weak quality/valuation. Score is unchanged. |

`tv_divergence` is interpretive overlay only — it does **not** move the rank.

**Caveats.** `tradingview-screener` is an *unofficial* client; the endpoint can
change. Anonymous access returns **delayed** data (the safe default here);
realtime fields require your own TradingView `sessionid` (set
`tradingview.sessionid` or export `TV_SESSIONID`). `.where()` filters are
AND-only; a screen needing OR logic must be hand-written. If the pull fails for
any reason, the screen still produces the full fundamental output with the TA
columns simply absent.

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The universe
builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

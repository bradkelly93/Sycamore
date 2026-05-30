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

You can overlay a TradingView technical signal onto the fundamental output. This
is an opt-in, **non-primary context** layer — by design it **never enters the
Q1/Q2/Q3 sub-scores, the composite, or the rank** (per `CLAUDE.md`: bottom-up
only, downside-first). It is added strictly to the *right* of the downside
(`negative_space`/`ns_flags`) columns so margin-of-safety stays the most
prominent read. Set `tradingview.mode` in `config.yaml`:

**`mode: csv` — for a CUSTOM Pine indicator (e.g. Trend Chameleon).**
TradingView's scanner API does **not** expose the output of custom Pine
indicators, so a live pull can't read them. Instead, export the tickers your
indicator flags into `data/raw/<csv_file>` (default `tradingview_screen.csv`).
The CSV needs a `ticker` column; everything else is optional:

```csv
ticker,passes_screen,regime
NASDAQ:AAPL,true,Bullish
NYSE:CW,false,Moderate Bear
```

- `ticker` — `AAPL` or `NASDAQ:AAPL` (exchange prefix is stripped).
- `passes_screen` — optional. If omitted, **presence in the file = passes** (so
  you can just export the names your indicator currently likes).
- any other columns (e.g. `regime`, a score) are carried through as `tv_<col>`.
- to dump a full export verbatim (every name + a label column), set
  `tradingview.signal_column` (e.g. `regime`) and `tradingview.pass_values`
  (e.g. `["Bull","Moderate Bull"]`) — membership is then derived from the label
  so you don't hand-filter.

How to produce it: TradingView's **Pine Screener** (Screener → add your
indicator → run over a watchlist → export), an alert log, or by hand. Re-export
to refresh; `tv_asof` shows the file's timestamp so staleness is visible.

**`mode: api` — for a built-in TradingView Stock Screener (RSI/SMA/market cap).**
Transcribe the screen's conditions into the `tradingview.filters` block
(declarative `{field, op, value}`, AND-combined) and we replicate it live via
`tradingview-screener`. Caveats: it's an *unofficial* client (endpoint can
change), anonymous access is **delayed** (realtime needs your own
`tradingview.sessionid` / `TV_SESSIONID`), and `.where()` is AND-only (OR logic
must be hand-written).

Either way:

```bash
sycamore-prep screen CW WES UMBF LECO MTDR --tv-overlay
sycamore-prep screen --sector industrials --tv-overlay --refresh-tv   # bypass the cache
```

The overlay adds, per name: `passes_screen`, any carried indicators
(`tv_regime`, `tv_RSI`, ...), `tv_asof`, and a neutral **`tv_divergence`** flag
derived *after the fact* from the **pure** composite crossed with membership:

| `tv_divergence` | meaning | downside-first reading |
|---|---|---|
| `agree_strong` | strong fundamentals **and** passes | thesis and tape agree |
| `agree_weak` | weak fundamentals **and** fails | neither likes it |
| `diverge_fund_strong_tech_fail` | strong fundamentals, fails | **research prompt, not a signal** — ask what the tape may be pricing that the filings haven't shown yet; re-examine the bear case. Score is unchanged. |
| `diverge_fund_weak_tech_pass` | weak fundamentals, passes | **do-not-chase prompt** — technical strength is not a reason to override weak quality/valuation. Score is unchanged. |

`tv_divergence` is interpretive overlay only — it does **not** move the rank. If
the overlay can't load (missing CSV, API failure), the screen still produces the
full fundamental output with the TA columns simply absent.

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The universe
builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

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

### Volatility overlay — tastytrade (downside cross-check)

An optional overlay that adds an **options-implied downside read** beside the
fundamental thesis. Sycamore's first principle is limiting permanent loss, so
the market's price for risk on a name is a useful second opinion on margin of
safety. It is **data-only**: your tastytrade account is used purely as an
authenticated gateway to market-level volatility metrics — it never reads
positions, never places orders, and **never feeds the three-attribute score**.
It only annotates.

Auth is OAuth2 (tastytrade discontinued username/password session-tokens on
2025-12-01). One-time setup in your tastytrade account: **OAuth Applications →
create an app** (save the *client secret*), then **Manage → Create Grant**
(save the *refresh token*; it never expires). Credentials live in the
environment, never `config.yaml` (which is committed):

```bash
export TASTYTRADE_CLIENT_SECRET=...
export TASTYTRADE_REFRESH_TOKEN=...
```

Per-ticker overlay, and the screener annotated with vol columns:

```bash
sycamore-prep vol CW
sycamore-prep vol CW --price 310 --mos-floor 280   # adds $ downside + MoS breach check
sycamore-prep vol CW --skew                        # also streams 25-delta put skew (see below)
sycamore-prep screen CW WES UMBF LECO MTDR --vol   # score/rank unchanged
```

What it surfaces (every row `source = tastytrade`, cached daily to
`data/cache/volatility_<TICKER>.parquet`):

- **IV rank / IV percentile** — how stressed the option market is on the name
  vs. its trailing year (high = market pricing more risk).
- **Expected move** (30-day and into the next earnings print) — the
  option-implied 1-sigma move; the downside leg is what we report.
- **`vol_flags`** — informational only: `elevated_iv_rank`, `high_iv_rank`,
  `earnings_imminent`, `thin_liquidity`, `implied_downside_breaches_mos`.
- **Margin-of-safety cross-check** — with `--price` and `--mos-floor`, flags
  when the option-implied 1-sigma-down price punctures your floor (in Phase 3
  that floor is the reverse-DCF downside).
- **Put skew (25-delta)** — with `--skew`, the put IV minus call IV at the
  25-delta wings, streamed per-strike from tastytrade's dxLink Greeks feed.
  Positive = the market is paying up for downside protection. This is the one
  piece that needs the websocket stream, so it's opt-in and requires the extra:
  `pip install 'sycamore-prep[vol]'`.

If credentials or network are unavailable the overlay **skips gracefully** —
the screener still produces all fundamental output, with a one-line note. Field
names are verified against the tastytrade SDK v12 schema, and parsing is
defensive (an unknown field shows blank rather than crashing).

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The same applies to
the tastytrade volatility overlay (`vol` / `screen --vol`): `api.tastyworks.com`
must be reachable and `TASTYTRADE_CLIENT_SECRET` / `TASTYTRADE_REFRESH_TOKEN`
set. The universe builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

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

### Phase 3 — Comps + Normalized Earnings + Reverse DCF

Run the full valuation workup for one subject against its peer set. Peers
default to the `peers` map in `config.yaml`; DCF defaults (WACC, terminal
growth, horizon) come from `valuation` and are overridable per run:

```bash
sycamore-prep comps CW                                   # peers from config.yaml
sycamore-prep comps CW --peers HEI,TDG,CACI              # explicit peer set
sycamore-prep comps CW --wacc 0.10 --terminal-growth 0.02 --years 10
sycamore-prep comps CW --share-basis wad                 # wad | shares_out | eps_implied
sycamore-prep comps CW --no-prices                       # skip price fetch; bands blank, comps still run
sycamore-prep comps CW --refresh                         # bypass caches; re-pull fundamentals + prices
```

Outputs `data/cache/comps_<TICKER>.xlsx` (tabs: `peer_comps`, `history_bands`,
`band_summary`, `normalized`, `reverse_dcf`, `sources`) and a markdown
tear-sheet `comps_<TICKER>.md`. The three valuation lenses are reported
**separately** per `CLAUDE.md` — never collapsed:

- **Discount to own history (headline).** For each multiple (P/E, EV/EBITDA,
  FCF yield; P/TBV for banks) the tool aligns the unadjusted FY-end close to
  each EDGAR fiscal year-end, builds the per-FY multiple, and reports where
  today's value sits as a percentile of the stock's own history (high =
  cheap). Needs ≥3 annual observations or it renders `n/a`.
- **Peer-relative.** Current multiples, normalized P/E, quality anchors
  (ROIC, FCF margin, leverage), and each peer's own-history percentile, plus
  `PEER_MEDIAN` / `PEER_MEAN` rows and the subject's P/E discount to the peer
  median. A peer that fails to load is kept as a visible error row, never
  silently dropped; missing values render `NaN`, not `0`.
- **Reverse DCF.** FCFF, end-of-year discounting, Gordon terminal, solved with
  a hand-rolled bisection (no scipy). **Bear → Base → Bull** (downside first):
  each case reports the **implied near-term FCFF growth** priced into today's
  EV *and* a **margin of safety** vs a conservative forward fair value. Banks
  show `reverse DCF = N/A` (FCF/EBITDA not meaningful) and rely on the P/E +
  P/TBV bands plus normalized EPS.

Normalized earnings use a **5-year mean operating margin applied to current
revenue** (interest subtracted before tax), with the trough-margin variant as
an explicit downside anchor.

Every figure is source-tagged: fundamentals are **EDGAR (primary)**; prices and
market cap are **yfinance (non-primary)**. Historical market cap pairs the
*unadjusted* FY-end close with as-reported diluted shares (splits cancel); the
chosen share basis is recorded on the `sources` tab.

**Re-pull after the synonym-picker fix.** The EDGAR synonym picker now prefers
the tag covering the most recent fiscal year (so revenue stays on the current
ASC-606 basis instead of a deprecated tag). **Re-pull with
`pull-fundamentals --refresh` before running comps** so cached parquets are
rebuilt — otherwise revenue-based figures may reflect a stale tag.

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The universe
builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

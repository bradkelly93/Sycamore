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

### Phase 4 — Spin-Off Tracker

Spin-offs are a classic special-situations *value* source: index/ETF funds are
forced to dump the orphaned SpinCo, creating temporary mispricing — but they
are also value traps when the parent loads the SpinCo with debt, pension, or
litigation. The tracker is **downside-first** (leverage / asset-quality /
"why was this spun" surfaced at least as prominently as upside) and decomposes
into the three attributes **separately** — never one opaque score.

```bash
sycamore-prep spinoffs scan                              # recent 10-12B registrations, market-wide
sycamore-prep spinoffs scan --lookback-days 730 --limit 50
sycamore-prep spinoffs track DHR --spinco VLTO           # deterministic parent↔SpinCo linkage
sycamore-prep spinoffs track MMM --spinco SOLV
sycamore-prep spinoffs show                              # reprint the last run (cached)
```

Two modes, both behind the swappable adapter layer (a new `FilingsProvider`
covering the EDGAR **submissions API** + **full-text search**):

- **`scan`** — broad, fast discovery via EDGAR full-text search for the Form 10
  family (`10-12B` + `/A`). One row per SpinCo (grouped by CIK) with status,
  amendment count, SIC, and the filing link. Metadata only (no per-SpinCo
  fundamentals) to respect SEC rate limits.
- **`track <PARENT>`** — deep-dive. Resolves the SpinCo (use `--spinco` for a
  deterministic link), pulls its primary-source XBRL **through the synonym-aware
  `get_financials`**, and computes the three-attribute / downside read.

Outputs `data/cache/spinoffs_<PARENT>.xlsx` (tabs: `tracker`, `downside_flags`,
`spinco_financials`, `filings`, `sources`) and a markdown tear-sheet alongside.

- **Downside flags** (computed from XBRL when the SpinCo has filed a 10-K):
  `high_leverage` (net debt/EBITDA > 4), `thin_interest_coverage` (< 3×),
  `negative_equity`, `negative_or_thin_fcf`, `declining_revenue`, plus the
  `forced_selling_window` around the distribution date.
- **Review prompts** that can't be machine-read are surfaced with the Form 10
  link rather than guessed: *why spun*, *pension / litigation / debt transfer*.
- **Distribution ratio + record/distribution dates** are parsed from the
  10-12B information statement **only when unambiguous** (tagged
  `derived (parsed 10-12B)`); otherwise they render **`pending`** with the
  filing link — never a guessed value.
- A freshly-registered SpinCo with no XBRL yet renders leverage/quality as
  **`pending`**, never `0`. Every row is source-tagged and traceable to an
  accession.

> **Network.** `scan` and the `track` enrichment hit `*.sec.gov` /
> `efts.sec.gov`, so run them on a host where SEC is reachable (see the sandbox
> note below). The discovery, flag, report, and parsing logic are fully
> offline-tested.

### Phase 5 — Excel Model Scaffolds

`build-models` writes a **lean, rebuildable** valuation workbook: driver/input
cells + **live Excel formulas** seeded from the Phase-3/4 engines, so you flex
assumptions in Excel and the reverse-DCF ties to the `comps <TICKER>` output.
These are **scaffolds to rebuild and internalize — not finished models, and not
to be presented as autogenerated** (see `models/MODEL_NOTES.md`).

```bash
sycamore-prep build-models CW                              # non-bank: FCFF DCF + reverse-DCF
sycamore-prep build-models CW --peers HEI,TDG,CACI         # explicit peer set for the football field
sycamore-prep build-models CW --wacc 0.10 --terminal-growth 0.02 --years 10
sycamore-prep build-models UMBF                            # bank auto-detected → P/TBV + normalized EPS
sycamore-prep build-models DHR --spinco VLTO               # adds the spin-off SOTP tab
sycamore-prep build-models CW --refresh                    # bypass caches; re-pull fundamentals + prices
```

Outputs `models/<TICKER>_model.xlsx` (gitignored — real data) with tabs:
**Inputs & Sources** (every driver named + source-tagged: input / formula /
EDGAR-primary / yfinance-non-primary / assumption), **DCF & Reverse-DCF** (or
**Bank Valuation** for banks), **Normalized Earnings**, **Football Field**,
**Spin-off SOTP** (with `--spinco`), and **Notes**. Downside-first throughout:
the **bear** case is the first data column and carries the loudest styling.

- The forward DCF is fully live off the `WACC` / `TERM_G` / `ASSUMED_G` drivers.
  The **reverse** implied growth is a bisection, so it is **seeded** from the
  engine with an Excel **tie-out CHECK** cell (residual ≈ 0 vs current EV on
  open); after flexing drivers, re-solve with **Goal Seek**.
- **Banks** (Deposits tag) drop the FCFF DCF / SOTP and value on P/TBV +
  normalized EPS.
- A committed **synthetic** example (`models/templates/example_synthetic_model.xlsx`,
  regenerate with `python scripts/make_example_model.py`) shows the layout
  without shipping real data.

> **openpyxl writes formula strings; it does not compute them.** Open the file in
> Excel and confirm no `#REF!`/`#DIV/0!`/`#NAME?`, that the reverse-DCF residual
> ties out (and matches `comps <TICKER>`), and that the bear column flexes. The
> test suite asserts the formula strings and proves the arithmetic matches the
> engine; only Excel proves evaluation.

### Phase 6 — Pipeline (one funnel, minimal intervention)

`pipeline` chains every phase into a single **downside-first funnel** — you run
one command and review a ranked shortlist:

```
universe → screen → shortlist (3-attribute) → per name: comps + model → ranked index
```

```bash
sycamore-prep pipeline                                   # top-10 of the universe
sycamore-prep pipeline --sector industrials --top 15     # sector slice
sycamore-prep pipeline --sycamore-only                   # only names Sycamore owns
sycamore-prep pipeline CW UMBF LECO                       # explicit list (skips the screen)
sycamore-prep pipeline --sector banks --sycamore-only --top 5   # filters compose
sycamore-prep pipeline --link-spinoffs                   # also flag recent spin-offs
```

Writes a self-contained run folder `data/cache/pipeline_<timestamp>/` (gitignored)
with one **dossier per name** (`<T>/comps_<T>.xlsx` + `<T>_model.xlsx`), a
downside-first **ranked index** (`index.xlsx` + `index.md`, margin-of-safety and
flags next to the three sub-scores), and a **`manifest.json`** (run params, the
peers used per name, and cache timestamps for reproducibility).

Clever bits, all reusing the existing engines (no recompute):
- **Cache = coordination.** Re-runs are idempotent/resumable and offline-replayable;
  only missing/stale data is re-pulled (`--refresh` forces a full re-pull).
- **Peer auto-selection.** Uses `config.peers[ticker]` when present, else derives a
  peer set from the universe by **GICS sector + nearest market cap**
  (`--no-auto-peers` to disable) — so any shortlisted name gets a comp set.
- **Cross-phase signal.** `is_bank` picks the model variant automatically;
  `--link-spinoffs` flags shortlisted names that appear in a recent Form-10 scan
  (run a targeted `build-models <PARENT> --spinco <SPINCO>` for an SOTP).
- **Resilient.** One bad name is recorded as a visible error row, never kills the run.

> **Network.** The screen + comps steps pull from EDGAR/yfinance, so run the
> pipeline where SEC is reachable. The selection, peer-derivation, and
> index/manifest assembly logic are fully offline-tested.

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

`www.sec.gov`, `data.sec.gov`, and `efts.sec.gov`. The EDGAR adapter (incl. the
spin-off submissions + full-text-search surface) is fully wired up, but
`pull-fundamentals` and `spinoffs scan` / `track` must be run on a host where
SEC endpoints are reachable (your laptop, or with a network policy that allows
SEC). The same applies to the tastytrade volatility overlay (`vol` /
`screen --vol`): `api.tastyworks.com` must be reachable and
`TASTYTRADE_CLIENT_SECRET` / `TASTYTRADE_REFRESH_TOKEN` set. The universe
builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

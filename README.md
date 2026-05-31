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

### Phase 6 — Prediction-market overlay (Polymarket)

A **non-primary, read-only** signal lens layered on the bottom-up tools. It
reads how Polymarket is positioned across four apertures around a name —
**company**, **peer** (reuses the `peers` map), **industry** (sector keywords),
and **macro** — to surface opportunity or risk the fundamental tools can't see.
Per `CLAUDE.md` it is walled off from the screener composite and every row is
tagged `source="polymarket (non-primary)"`. No trading — read-only market data.

**1. Discover candidate markets.** Auto-matches markets to your names with a
transparent `relevance_score` and upserts them into an editable mapping CSV:

```bash
sycamore-prep prediction-discover CW WES UMBF
sycamore-prep prediction-discover --universe --limit 50
sycamore-prep prediction-discover SAVE --apertures company,peer
```

**Noise filtering (company + peer).** Short peer tickers collide with everyday
words — `ET` (Energy Transfer) with "Eastern Time", `PR` (Permian Resources)
with "People's Republic", `ASB` (Associated Banc-Corp) with "ASB Classic" — so
the peer aperture searches by *company name* (resolved via EDGAR), not ticker,
and the company + peer apertures drop crypto/sports/pop-culture markets
(`prediction.noise_category_tokens` in `config.yaml`, freely editable).

**Crypto in macro is deliberate.** Crypto is filtered out of company/peer (no
per-company signal) but *kept* in the **macro** aperture as a risk-appetite /
liquidity gauge — a long-dated `"Bitcoin reach 2026"` market scoped to risk-on
sectors. Threshold-laddered markets (the Bitcoin/WTI price strikes) are capped
to `prediction.max_markets_per_query` (default 3) per query, ranked by relevance
then traded volume. Macro stays segregated — it never feeds the screener.

Then open `data/raw/prediction_markets.csv`, set `confirmed=True` on the rows
that genuinely attach to a name, and fix `event_type` / `direction` if the
keyword guess is off. Re-running discovery refreshes the machine fields
(`relevance_score`, `question`) but **never overwrites your edits**.

**2. Build the overlay.** Pulls current implied probabilities; downside
read-throughs sort to the top (downside-first):

```bash
sycamore-prep prediction-overlay CW WES
sycamore-prep prediction-overlay --downside-only        # only RISK read-throughs
sycamore-prep prediction-overlay --include-macro        # add the macro aperture (segregated)
sycamore-prep prediction-overlay --include-unconfirmed  # use candidates before confirming
```

Output (`data/cache/prediction_overlay.xlsx` + `.csv`) carries, per market: the
implied probability, its 30-day move (`prob_chg_30d` — the positioning signal),
liquidity/volume, resolution date, the `read_through` (RISK / OPPORTUNITY /
WATCH), `relevance_score`, and the `source` tag.

**3. Annotate the screener (optional).** Adds `event_*` columns — never touches
`composite_rank`:

```bash
sycamore-prep screen CW WES UMBF --with-prediction-overlay
```

`event_contradiction=True` flags an otherwise high-ranked, clean name that
nonetheless carries a material market-implied RISK — exactly the divergence the
bottom-up screen can't see.

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The universe
builder, tests, and Excel scaffolds work offline.

The same applies to Polymarket (`gamma-api.polymarket.com`): the overlay is
fully wired and offline-tested against fixtures, but `prediction-discover` and
`prediction-overlay` must run where Polymarket is reachable. The mapping CSV,
the relevance scorer, and the overlay assembly all work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

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

## Notes on the remote execution sandbox

The Claude Code on the web container's egress policy blocks
`www.sec.gov` and `data.sec.gov`. The EDGAR adapter is fully wired up, but
`pull-fundamentals` must be run on a host where SEC endpoints are reachable
(your laptop, or with a network policy that allows SEC). The universe
builder, tests, and Excel scaffolds work offline.

## Non-goals

No web UI, no paid data services, no backtester, no macro overlay. This is
bottom-up decision support — see `CLAUDE.md` for the full list.

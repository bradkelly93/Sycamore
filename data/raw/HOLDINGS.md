# Holdings CSVs — what `build-universe` expects

`sycamore-prep build-universe` builds the investable universe from up to three
holdings CSVs you drop in this folder (`data/raw/`). This file documents the
**exact** columns it looks for so you can drop the right files in without
guessing. (The CSVs themselves are gitignored — only this doc is committed.)

The matching logic lives in `src/sycamore_prep/universe/builder.py`; this doc
mirrors it.

## The three files

| File (default name) | Source | Required? |
|---|---|---|
| `iws_holdings.csv` | iShares Russell Mid-Cap Value ETF (**IWS**) | at least one of the three |
| `iwn_holdings.csv` | iShares Russell 2000 Value ETF (**IWN**) | at least one of the three |
| `sycamore_holdings.csv` | Sycamore / Victory fund holdings overlay | optional |

- Names are configurable in `config.yaml` under `raw:` and overridable per run:
  `build-universe --iws <path> --iwn <path> --sycamore <path>`.
- **At least one** file must be present, or the command raises
  `FileNotFoundError`. Each present file must have a ticker **and** a name
  column (see below) or it raises `ValueError` listing the columns it found.
- `owned_by_sycamore=True` is set for every ticker that appears in
  `sycamore_holdings.csv`; `in_iws` / `in_iwn` likewise. A ticker in more than
  one file is aggregated to a single row (its `source` lists every contributor;
  `market_cap` is the max across files).

## Columns the builder reads

Header matching is **case-insensitive** and ignores surrounding whitespace, so
`Ticker`, `ticker`, and ` TICKER ` all match. For each field the builder takes
the **first** matching column it finds, in this order:

| Field | Accepted column headers (any one) | Required? |
|---|---|---|
| **Ticker** | `ticker`, `issuer ticker`, `symbol`, `holding ticker` | **yes** |
| **Name** | `name`, `issuer name`, `security name`, `holding name` | **yes** |
| **GICS sector** | `sector`, `gics sector`, `industry sector` | no (→ blank) |
| **Market cap** | `market value`, `market cap`, `marketcap`, `market capitalization` | no (→ blank) |
| **Weight %** | `weight (%)`, `weight(%)`, `% of net assets`, `weight`, `portfolio weight` | no (→ blank) |

Any other columns in the file are ignored.

## iShares exports work as-is — drop them in unchanged

The default iShares "Detailed Holdings and Analytics" CSV export ships with
~9 lines of fund metadata **above** the real column header. You do **not** need
to strip them: the builder sniffs the first ~40 rows for the first line that
contains both a ticker and a name column and treats that as the header.

A standard iShares export already uses `Ticker`, `Name`, `Sector`,
`Market Value`, and `Weight (%)` headers, which map directly onto the fields
above. To get one: iShares product page → **Holdings** →
**Detailed Holdings and Analytics** → Download (CSV).

## Parsing / cleaning rules

- Tickers are upper-cased and stripped.
- `Market Value` / `Weight (%)` have `,` and `%` removed and are coerced to
  numbers; non-numeric cells become blank (`NaN`), never `0`.
- Rows are dropped when the ticker isn't 1–10 characters or is one of
  `-`, `USD`, `CASH` (iShares cash / non-equity lines).
- Class shares keep their dot (e.g. `MOG.A`, `BRK.B`) — that matches this
  repo's ticker convention (see `peers` in `config.yaml`).

## Minimal hand-rolled example

If you're building a `sycamore_holdings.csv` by hand, this is enough — only
`ticker` and `name` are mandatory:

```csv
ticker,name,sector,market value,weight (%)
CW,Curtiss-Wright Corp,Industrials,1200000,2.5
UMBF,UMB Financial Corp,Financials,800000,1.6
LECO,Lincoln Electric Holdings,Industrials,650000,1.3
```

After dropping the file(s) in, run:

```bash
sycamore-prep build-universe
sycamore-prep show-universe --n 20
```

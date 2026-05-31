# VERIFY.md — live verification runbook (owner-run)

Everything in this repo is offline/fixture-tested (`pytest -q` → **191 passing**).
The checks below are the ones a sandbox **cannot** run because they need live
network + credentials: SEC EDGAR (`*.sec.gov`), tastytrade, TradingView, and
Polymarket. Run them on your laptop (or a network policy that allows those
hosts). Each section is the exact command and the **pass signal** to look for.

```bash
# 0. Preconditions (once)
pip install -e ".[dev]"            # add ".[dev,vol]" for put-skew streaming
#   edit config.yaml → edgar.user_agent = "sycamore-prep/0.1 you@example.com"
sycamore-prep config-check
#   PASS: prints your User-Agent, the cache dir, configured peers, and the
#         Polymarket Gamma URL with no traceback.
```

## 1. Fundamentals + universe build (SEC reachable)

```bash
sycamore-prep pull-fundamentals CW WES UMBF LECO MTDR
#   PASS: one green "[<T>] cached <N> rows across <M> concepts → .../data/cache"
#         per ticker; data/cache/financials_<T>.parquet files appear.

# Drop the holdings CSVs in data/raw/ first (see data/raw/HOLDINGS.md):
#   iws_holdings.csv, iwn_holdings.csv, sycamore_holdings.csv (≥1 required)
sycamore-prep build-universe
#   PASS: green "Universe: <N> tickers (IWS=<a>, IWN=<b>, Sycamore overlay=<c>)"
#         and data/cache/universe.parquet + universe.csv are written.
sycamore-prep show-universe --n 20
#   PASS: prints 20 rows with in_iws / in_iwn / owned_by_sycamore / source.
```

## 2. Screener + comps + models + pipeline (SEC + yfinance)

```bash
sycamore-prep screen CW WES UMBF LECO MTDR
#   PASS: green "Scored 5 tickers → .../screener_output.xlsx"; the printed table
#         shows composite_rank + q1/q2/q3 sub-scores SEPARATELY (never one number).

sycamore-prep comps CW
#   PASS: writes comps_CW.xlsx + comps_CW.md; prints own-history cheap-percentiles
#         and a "reverse DCF (base): implied FCFF growth …% | margin of safety …%".

sycamore-prep build-models CW
#   PASS: writes models/CW_model.xlsx. Open it in Excel and confirm: no
#         #REF!/#DIV/0!/#NAME?, the reverse-DCF tie-out CHECK ≈ 0, the BEAR
#         column (first data column) flexes when you change WACC/TERM_G.

sycamore-prep pipeline CW UMBF LECO --top 3
#   PASS: a data/cache/pipeline_<ts>/ folder with one dossier per name, index.xlsx
#         + index.md (MoS + flags beside the sub-scores), and manifest.json.
```

## 3. Spin-off tracker (SEC full-text search reachable)

```bash
sycamore-prep spinoffs scan --lookback-days 365
#   PASS: green "Found <N> Form-10 registrations → .../spinoffs_scan.xlsx".
sycamore-prep spinoffs track DHR --spinco VLTO
#   PASS: per-spin line with [status] + downside flags (or "pending" if no XBRL
#         yet — never a guessed 0).
```

## 4. Volatility overlay — tastytrade (downside cross-check)

```bash
export TASTYTRADE_CLIENT_SECRET=...      # OAuth app client secret
export TASTYTRADE_REFRESH_TOKEN=...      # personal grant refresh token
sycamore-prep vol CW
#   PASS: a table with iv_rank / iv_percentile / iv_index / expected_move_30d_pct
#         and a green "source: tastytrade — downside cross-check only".
#   (No creds? You get a clear RED message telling you which env vars to set.)

sycamore-prep vol CW --price 310 --mos-floor 280
#   PASS: adds sigma_down_30d_price; vol_flags shows implied_downside_breaches_mos
#         when the 1-sigma-down price punctures the floor.

sycamore-prep vol CW --skew                 # needs ".[vol]" (websockets)
#   PASS: a put_skew_25d column (positive = market paying up for downside puts).

# Wall-off invariant — the overlay must NOT move the score:
sycamore-prep screen CW WES UMBF LECO MTDR            # note the composite_rank
sycamore-prep screen CW WES UMBF LECO MTDR --vol      # IV columns appended
#   PASS: composite_rank is IDENTICAL across the two runs; vol columns sit to the
#         RIGHT of the downside flags.
```

## 5. TradingView technical overlay (non-primary context)

```bash
# mode: trend (config default — NO TradingView account needed)
sycamore-prep screen CW WES UMBF LECO MTDR --tv-overlay
#   PASS: passes_screen + tv_divergence + tv_pct_above_200 / tv_slope200_pct
#         columns appear; composite_rank is unchanged vs the plain screen.

# mode: api (set tradingview.mode: api + filters in config.yaml; live screener)
#   export TV_SESSIONID=...   # optional, only for realtime (else delayed data)
sycamore-prep screen CW WES UMBF LECO MTDR --tv-overlay --refresh-tv
#   PASS: live membership from your filters; "[tv-overlay] WARNING: ... truncated"
#         only if you exceed max_results.

# Degradation sanity check: set tradingview.enabled: false in config.yaml, then
sycamore-prep screen CW WES --tv-overlay
#   PASS: full fundamental output + a YELLOW note "tv overlay skipped: set
#         tradingview.enabled: true ..." (never a silent no-op).
```

## 6. Prediction-market overlay — Polymarket (gamma-api reachable)

Discover → confirm → overlay:

```bash
sycamore-prep prediction-discover CW WES UMBF
#   PASS: a green "[<T>] <N> candidate market(s)" line per name, then
#         "Mapping: <N> rows (<u> unconfirmed) → .../data/raw/prediction_markets.csv".

#   ↓ EDIT data/raw/prediction_markets.csv: set confirmed=True on the rows that
#     genuinely attach to a name; fix event_type / direction if the guess is off.

sycamore-prep prediction-overlay CW WES
#   PASS: green "Overlay: <N> market(s) across <M> name(s) → .../prediction_overlay.xlsx";
#         RISK read-throughs sort to the top (downside-first).

# Wall-off invariant on the screener annotation:
sycamore-prep screen CW WES UMBF --with-prediction-overlay
#   PASS: event_top_prob / event_contradiction columns appear; composite_rank is
#         unchanged vs the plain screen. If you instead see the YELLOW note
#         "prediction overlay: no confirmed market mappings surfaced", you have
#         not set confirmed=True yet (or egress to Polymarket is blocked).
```

---

**One-line gut check after any run:** the three overlays (vol / TradingView /
prediction) only ever ADD columns to the right of the downside flags — if a
`composite_rank` ever changes when you toggle `--vol` / `--tv-overlay` /
`--with-prediction-overlay`, that is a wall-off regression, not expected behavior.

# Sycamore Toolkit — Unified UI Build Spec (handoff)

> **Purpose of this doc.** A self-contained brief for a new chat to build a
> single user interface over the existing Sycamore CLI toolkit. It captures the
> decisions already made, the engine entry points to wrap, the hard design
> constraints, and a phased build plan. Read `CLAUDE.md` first for the operating
> philosophy — the UI must preserve it, not bypass it.

## 0. Decisions already made (locked)

| Axis | Decision |
|---|---|
| **Shape** | **Hybrid** — an Explorer over the cache + pipeline runs, PLUS safe single-name triggers (comps / build-models / vol) and an inline prediction-mapping confirm table. Heavy jobs run in the background. |
| **Stack** | **Streamlit** — pure Python over the existing engine functions; no JS, no second codebase. |
| **Reach** | **Local, single-user** — `streamlit run`, localhost only. **No auth, no server, no DB.** Credentials via env vars (already how the CLI works). |
| **Long jobs** | **Background task + live progress panel in the UI** (universe pull, full pipeline); UI stays responsive. Fast single-name actions run inline. |

These came from an explicit owner decision that **lifts the CLAUDE.md "no web UI"
non-goal**. The first build task is to update CLAUDE.md's non-goals + repo map to
reflect that the UI is now in scope (a thin, local, read-mostly lens — still no
auth/DB/hosting).

## 1. Guiding principle: the UI is a thin lens, not a second system

The library is already UI-ready — **every engine returns a rich object**, so the
UI calls the function and renders the return value. Do NOT reimplement any
analytics in the UI layer. Minimal-to-no refactor of the engines is expected; if
a function only writes a file, prefer using its already-returned object.

No gold-plating: this is a personal productivity tool for one analyst, run
locally. Keep it lean.

## 2. Engine entry points to wrap (all in `src/sycamore_prep/`)

| Tool | Function | Returns | Notes for UI |
|---|---|---|---|
| Pull fundamentals | `adapters.EdgarProvider.get_financials(ticker)` | `FinancialsFrame` | cached parquet; SEC rate-limited (~10 req/s) |
| Build universe | `universe.builder.build_universe()` / `load_universe()` | DataFrame | needs IWS/IWN holdings CSVs in `data/raw/` (see `data/raw/HOLDINGS.md`) |
| Screener | `screener.run_screener(tickers, sector, …, with_vol, tv_overlay, with_prediction_overlay)` | DataFrame (indexed by ticker) | sub-scores `q1/q2/q3_*_score`, `composite_rank`, `negative_space`, `ns_flags`; overlay flags add columns to the RIGHT, never change rank |
| Comps + reverse DCF | `comps.run_comps(ticker, peers, …)` | `CompsResult` (`subject: TickerAnalysis`, `peer_table`, dcf_cases, normalized, history) | also writes xlsx+md to cache |
| Excel model scaffold | `models.build_models(ticker, …, comps_result=…)` | `ModelBuildResult` (xlsx_path, is_bank, has_sotp) | writes `models/<T>_model.xlsx`; **UI links/downloads it, never reimplements the live Excel formulas** |
| Pipeline (funnel) | `pipeline.run_pipeline(tickers/sector/sycamore_only/top, …)` | `PipelineResult` (shortlist, dossiers, out_dir, index/manifest paths) | emits `data/cache/pipeline_<ts>/` with `index.xlsx` + `index.md` + `manifest.json` + per-ticker dossiers — basically a report folder to render |
| Spin-offs | `spinoffs.tracker.run_scan(...)` / `run_track(parent, spinco)` | `TrackerResult` | downside flags, soft fields |
| Volatility overlay | `metrics.volatility.volatility_overlay(...)` via `adapters.TastytradeProvider` | dict / VolatilityFrame | needs `TASTYTRADE_CLIENT_ID` + `CLIENT_SECRET` + `REFRESH_TOKEN` env (all same app); `--skew` needs `[vol]` extra (websockets) |
| Technical overlay | `adapters` TradingView/trend providers via `run_screener(tv_overlay=True)` | columns on screener df | `trend` mode needs no creds |
| Prediction overlay | `prediction_markets.discover_for_ticker(...)`, `mapping.load_mapping/upsert/save_mapping`, `overlay.build_overlay(...)` | DataFrames | **human-in-the-loop confirm step → ideal UI win** (checkbox table instead of editing CSV) |
| Config check | `config.load_config()` | `AppConfig` | surfaces peers, valuation defaults, overlay config |

All caches live in `data/cache/` (gitignored); `config.cache_dir()`, `models_dir()`, `raw_dir()` give paths.

## 3. Hard design constraints (preserve the toolkit's ethos)

A slick UI is exactly how a disciplined tool becomes the opaque black box
`CLAUDE.md` forbids. The UI MUST:

1. **Source tags visible everywhere** — badge every figure `edgar (primary)` /
   `yfinance (non-primary)` / `tastytrade` / `polymarket (non-primary)`.
2. **Downside-first visual hierarchy** — margin of safety, bear case, downside
   flags rendered at least as prominently as upside; red/amber for risk.
3. **Never collapse the three attributes** — show Q1 Quality / Q2 Valuation /
   Q3 Improving as separate bars/columns; `composite_rank` is only a sort key.
4. **Overlays segregated + labelled NON-PRIMARY** — vol / technical /
   prediction in a clearly-walled-off panel; mirror the code's invariant that
   they never move the rank (and ideally show a toggle that proves rank is
   unchanged).
5. **Excel stays Excel** — link/download the model workbooks; do NOT rebuild the
   live-formula flexing in the UI. The models are deliberately rebuildable Excel
   artifacts for the owner to flex/internalize (and never to be presented as
   autogenerated).
6. **Auditable** — every rendered number traceable to its engine output; offer
   "download the underlying xlsx/csv" on every view.

## 4. Recommended layout (Streamlit, hybrid)

- **Sidebar:** global controls — ticker / peer-set / sector pickers, overlay
  toggles (`--vol`, `--tv-overlay`, `--with-prediction-overlay`), config status
  (peers, WACC/terminal-growth defaults, which creds are detected).
- **Pages (or tabs):**
  1. **Universe** — status (is `universe.parquet` built? counts), holdings-CSV
     drop instructions, "build universe" action.
  2. **Screener** — run the three-attribute screen on tickers/sector; render the
     ranked table with the three sub-scores as separate bars + negative-space
     flags + (optional) overlay columns to the right.
  3. **Name workup** — pick a ticker → comps (peer table, own-history bands,
     normalized earnings, reverse-DCF bear/base/bull with MoS) + a button to
     build the Excel model (download link) + the vol panel.
  4. **Pipeline** — kick off `run_pipeline` (background + progress), then render
     the latest run folder: ranked index (downside-first) + drill into each
     dossier. Render existing `pipeline_<ts>/` folders too.
  5. **Spin-offs** — scan / track views.
  6. **Overlays** — vol, technical, and the **prediction-mapping confirm table**
     (checkbox `confirmed` column writing back to `prediction_markets.csv` via
     `mapping.upsert/save_mapping`) — the standout UI improvement over the CLI.
- **Background jobs:** a simple thread/`concurrent.futures` runner + a status
  panel (Streamlit reruns on interaction, so long jobs must not block the main
  thread; poll a job-state object). Heavy = universe pull, full pipeline.

## 5. Cross-cutting concerns

- **Creds:** read from env (same as CLI). Sidebar shows ✓/✗ for SEC user-agent,
  tastytrade (`CLIENT_ID`+`CLIENT_SECRET`+`REFRESH_TOKEN`, same app), TradingView
  sessionid (optional). Never store secrets; never write them to disk.
- **Network/blocked-egress:** every networked action must degrade gracefully with
  a clear message (the engines already do this — surface the note, never a
  traceback). Prefer reading cache first; pull only on explicit action.
- **Reproducibility:** show `manifest.json` (run params, cache timestamps, peers
  used, `peers_source`) on pipeline views so a run is auditable.
- **Windows + venv:** the owner runs `.\.venv\Scripts\python.exe`. Provide a
  `streamlit run` launch command (e.g. `.\.venv\Scripts\streamlit.exe run app.py`)
  and add `streamlit` under an optional extra (e.g. `[ui]`) in `pyproject.toml`.

## 6. Phased build plan (suggest to the build chat)

- **Phase A — Explorer (read-only, no jobs):** render existing cache + the latest
  `pipeline_<ts>/` folder (index + dossiers), screener output, comps xlsx/md,
  with all source-tag/downside/three-attribute rendering rules. Proves the lens
  + the design constraints with zero job-management risk.
- **Phase B — Safe single-name triggers:** comps / build-models / vol for one
  ticker, inline with `st.status` progress. Add the prediction-mapping confirm
  table (write-back).
- **Phase C — Background heavy jobs:** universe build + full `run_pipeline` as
  background tasks with a live progress panel.

Ship A first; it's the most value for the least risk and locks the visual/ethos
conventions before any concurrency.

## 7. Testing / acceptance

- The UI is a thin layer, so keep logic out of it; anything non-trivial (e.g. a
  job runner, a mapping-writeback helper) gets a unit test. Don't try to
  "pytest the Streamlit widgets."
- Acceptance: launch locally; for each page confirm it renders real engine output
  with source tags + downside-first + separate sub-scores; confirm an overlay
  toggle never changes `composite_rank`; confirm model downloads open in Excel;
  confirm a blocked/credential-less overlay shows a clear note, not a crash.
- Keep the existing 208-test suite green; the UI must not require engine changes
  that break it.

## 8. Current state (as of this handoff)

- Branch: `claude/dazzling-lamport-wQ0M5` (tip `4131ebd`), **208 tests passing**,
  CI green on 3.11/3.12.
- All engines + 4 overlays are built and live-verified. `VERIFY.md` is the
  manual runbook; `data/raw/HOLDINGS.md` documents the universe CSVs.
- Build the UI on this branch (or a feature branch off it); update CLAUDE.md's
  non-goals + repo map as the first commit.

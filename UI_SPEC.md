# Sycamore Toolkit — Unified UI Build Spec (handoff)

> **Purpose of this doc.** A self-contained brief for a new chat to build a
> single user interface over the existing Sycamore CLI toolkit. It captures the
> decisions already made, the engine entry points to wrap, the hard design
> constraints, and a phased build plan. Read `CLAUDE.md` first for the operating
> philosophy — the UI must preserve it, not bypass it.

## 0. Decisions already made (locked)

| Axis | Decision |
|---|---|
| **Reach** | **Local, single-user** — localhost only. **No auth, no server, no DB.** Credentials via env vars (already how the CLI works). |
| **Long jobs** | **Background task + live progress panel** (universe pull, full pipeline); UI stays responsive. Fast single-name actions run inline. |
| **Shape** | Evaluated as a **3-tier progression**, not three separate apps: Explorer ⊂ Hybrid ⊂ Full control panel. Built in phases A→B→C; owner judges at each tier. |
| **Stack** | **Bake-off between Streamlit and FastAPI+web** (Textual TUI dropped). Decided by building one tier in both, then continuing the winner. |

### The build strategy (important — this is a stack bake-off, not 6 apps)

The owner wants to choose from *working* prototypes rather than on paper. The
cost-sane way to do that:

1. **Phase A (Explorer, read-only) is built TWICE — once in Streamlit, once in
   FastAPI + a light web frontend (HTMX or minimal React).** Same feature set,
   same data, same design constraints (§3). This is the real, usable comparison.
2. **Owner picks the stack** from the two Explorers.
3. **The winning stack continues** through Phase B (Hybrid: single-name triggers
   + prediction-mapping confirm) and Phase C (Full control panel: background
   universe/pipeline jobs). The losing stack is dropped after Phase A.

Net: **one genuine stack comparison + all three shapes seen**, without building
3×2 = 6 apps. Keep the two Phase-A builds behind a shared, UI-agnostic service
layer (§4a) so ~all the non-rendering code is reused across both and into B/C.

This lifts the CLAUDE.md "no web UI" non-goal by explicit owner decision. **First
build commit:** update CLAUDE.md's non-goals + repo map to reflect the UI is now
in scope (a thin, local, read-mostly lens — still no auth/DB/hosting).

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

## 4. Shared service layer (build FIRST — this is what makes the bake-off cheap)

The two Phase-A builds (Streamlit + FastAPI) must NOT each re-glue the engines.
Build one **UI-agnostic service layer** that both front-ends import, so the only
stack-specific code is rendering. This is also the seam that keeps the UI a thin
lens (§1) and carries forward into Phase B/C and whichever stack wins.

- **Location:** `src/sycamore_prep/service/` (a new package — pure Python, no UI
  imports, no Streamlit/FastAPI deps). Unit-testable like any engine code.
- **Responsibility:** call the engine entry points (§2), normalize their returns
  into plain serializable view-models (dataclasses / dicts / DataFrames) that
  already carry the §3 metadata — source tags, the three sub-scores kept
  separate, downside fields flagged, overlay columns marked non-primary. A
  front-end should be able to render purely from these without re-deriving
  anything.
- **Shape (illustrative, not prescriptive):**
  - `service.screener.run(...) -> ScreenerView` (ranked rows + per-row source
    tags + overlay-column provenance + a `rank_unchanged_by_overlays` checkable)
  - `service.workup.for_ticker(ticker, ...) -> NameWorkupView` (comps + DCF
    cases + normalized + model xlsx path + vol panel)
  - `service.pipeline.latest() / .list_runs() / .load(run_dir) -> PipelineView`
    (reads the `pipeline_<ts>/` folder: index, dossiers, manifest)
  - `service.prediction.candidates(...)`, `.confirm(updates) -> MappingView`
    (wraps `mapping.load_mapping/upsert/save_mapping` for the confirm table)
  - `service.jobs` — a tiny background-job runner (thread/`concurrent.futures` +
    a poll-able job-state object) used by Phase C; stack-agnostic.
  - `service.creds.status() -> dict` (which env creds are detected; never values)
- **Serializable by design:** because FastAPI will JSON these over HTTP and
  Streamlit will render them in-process, the view-models must be JSON-friendly
  (DataFrames → `to_dict`/records at the boundary). This constraint is free
  insurance that the layer stays presentation-agnostic.

**Both Phase-A Explorers are then ~just rendering** of identical service outputs
— a fair stack comparison, and ~80% of the code is shared and survives the
bake-off.

## 5. UI layout (stack-agnostic; both Phase-A builds render these views)

Described as pages/sections; Streamlit renders them as pages/tabs, FastAPI as
routes + templates. Both pull from the §4 service layer.

- **Sidebar / global controls:** ticker / peer-set / sector pickers, overlay
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
     the service layer / `mapping.upsert/save_mapping`) — the standout UI
     improvement over the CLI.
- **Background jobs (Phase C):** via `service.jobs` — heavy = universe pull, full
  pipeline. In Streamlit, long jobs must not block the rerun loop (poll the
  job-state object); in FastAPI they're a natural async/background task.

## 6. Cross-cutting concerns

- **Creds:** read from env (same as CLI). Sidebar shows ✓/✗ for SEC user-agent,
  tastytrade (`CLIENT_ID`+`CLIENT_SECRET`+`REFRESH_TOKEN`, same app), TradingView
  sessionid (optional). Never store secrets; never write them to disk.
- **Network/blocked-egress:** every networked action must degrade gracefully with
  a clear message (the engines already do this — surface the note, never a
  traceback). Prefer reading cache first; pull only on explicit action.
- **Reproducibility:** show `manifest.json` (run params, cache timestamps, peers
  used, `peers_source`) on pipeline views so a run is auditable.
- **Windows + venv:** the owner runs `.\.venv\Scripts\python.exe`. Each stack
  gets its own optional extra in `pyproject.toml` (e.g. `[ui-streamlit]`,
  `[ui-web]`) and a one-line launch command (Streamlit:
  `.\.venv\Scripts\streamlit.exe run app_streamlit.py`; FastAPI:
  `.\.venv\Scripts\uvicorn.exe sycamore_prep.web:app`). The service layer (§4)
  needs no extra deps.

## 7. Phased build plan (the bake-off)

**Order is deliberate: shared service layer → two Explorers → pick → grow winner.**

- **Phase 0 — Service layer (§4).** Build `src/sycamore_prep/service/` + its unit
  tests first. No UI yet. This de-risks everything and is the shared substrate.
- **Phase A — Explorer (read-only), built in BOTH stacks:**
  - **A-Streamlit** and **A-FastAPI** — same views (§5), both rendering the §4
    service outputs: existing cache + latest `pipeline_<ts>/` (index + dossiers),
    screener output, comps xlsx/md — with all source-tag / downside-first /
    three-attribute rules. No job management.
  - **→ Owner compares the two and picks the stack.** Drop the loser.
- **Phase B — Hybrid (winning stack only):** safe single-name triggers (comps /
  build-models / vol for one ticker, inline progress) + the prediction-mapping
  confirm table (write-back via the service layer).
- **Phase C — Full control panel (winning stack only):** universe build + full
  `run_pipeline` as background jobs (`service.jobs`) with a live progress panel.

Ship Phase 0 + both Phase-A Explorers first; that's the decision point. Lock the
visual/ethos conventions in Phase A before any concurrency (B/C).

## 8. Testing / acceptance

- **Test the service layer (§4), not the widgets.** All non-rendering logic lives
  there and gets unit tests (view-model shape, source tags present, overlay
  columns marked non-primary, rank-unchanged invariant, mapping write-back, the
  job runner). Don't try to "pytest the Streamlit widgets" or scrape HTML.
- Acceptance (per Phase-A Explorer, both stacks): launch locally; each view
  renders real engine output with source tags + downside-first + separate
  sub-scores; an overlay toggle never changes `composite_rank`; model downloads
  open in Excel; a blocked/credential-less overlay shows a clear note, not a
  crash. The two Explorers should be feature-identical so the comparison is about
  stack feel, not coverage.
- Keep the existing 208-test suite green; the UI must not require engine changes
  that break it. New tests are additive (service layer + job runner).

## 9. Current state (as of this handoff)

- Branch: `claude/dazzling-lamport-wQ0M5`, **208 tests passing**, CI green on
  3.11/3.12. (Confirm the live tip with `git log --oneline -1` before building.)
- All engines + 4 overlays are built and live-verified. `VERIFY.md` is the
  manual runbook; `data/raw/HOLDINGS.md` documents the universe CSVs;
  `models/MODEL_NOTES.md` the model methodology.
- Build the UI on this branch (or a feature branch off it). **First commit:**
  update CLAUDE.md's non-goals + repo map (web UI now in scope: thin, local,
  no auth/DB/hosting). **Second:** the §4 service layer. Then the bake-off.

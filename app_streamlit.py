"""Streamlit read-only Explorer (Phase A bake-off).

A thin lens over ``sycamore_prep.service`` — it renders the same view-models the
FastAPI Explorer does; only the rendering differs. No analytics here. Launch:

    streamlit run app_streamlit.py

Phase A is READ-ONLY: screener/workup are the core (cached) reads; there are no
build / scan / pipeline-run / universe-build actions (Phase B/C).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from sycamore_prep import service

st.set_page_config(page_title="Sycamore Explorer", layout="wide")

PRIMARY, NONPRIMARY = "🟢", "⚪"


# --------------------------------------------------------------------------- #
# Display helpers (rendering only — the §3 metadata is already on each Figure)
# --------------------------------------------------------------------------- #
def fmt_value(value, fmt: str = "raw") -> str:
    if value is None:
        return "—"
    try:
        if fmt == "pct":
            return f"{float(value) * 100:.1f}%"
        if fmt == "mult":
            return f"{float(value):.1f}×"
        if fmt == "ccy":
            return _ccy(float(value))
        if fmt == "int":
            return f"{int(value):,}"
        if isinstance(value, float):
            return f"{value:.2f}"
    except (TypeError, ValueError):
        return str(value)
    return str(value)


def _ccy(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.2f}"


def fig_text(f) -> str:
    """Value + a primary/non-primary marker, so a source tag rides every number."""
    if f is None or f.value is None:
        return "—"
    return f"{fmt_value(f.value, f.fmt)} {PRIMARY if f.primary else NONPRIMARY}"


def metric(col, label: str, f, *, delta_risk: bool = False):
    if f is None:
        col.metric(label, "—")
        return
    delta = None
    if delta_risk and f.flag == "risk":
        delta = "downside"
    col.metric(label, fig_text(f), delta=delta, delta_color="inverse" if delta else "normal",
               help=f"source: {f.source}")


def download_fig(ref, label: str):
    if not ref:
        return
    try:
        path = service.artifacts.resolve(ref.token)
        st.download_button(f"⬇ {label}", data=path.read_bytes(), file_name=ref.filename,
                           key=f"dl-{ref.token}")
    except (KeyError, PermissionError, FileNotFoundError):
        st.caption(f"{label}: unavailable")


# --------------------------------------------------------------------------- #
# Sidebar — global controls + creds (✓/✗ presence only)
# --------------------------------------------------------------------------- #
def sidebar():
    st.sidebar.title("Sycamore Explorer")
    st.sidebar.caption("read-only · localhost · Streamlit")
    page = st.sidebar.radio(
        "Page", ["Universe", "Screener", "Name workup", "Pipeline", "Spin-offs", "Overlays"])

    st.sidebar.subheader("Screener / workup controls")
    st.session_state.setdefault("tickers", "")
    st.sidebar.text_input("Tickers (space/comma)", key="tickers", placeholder="CW WES UMBF")
    st.sidebar.text_input("Sector", key="sector", placeholder="Industrials")
    st.sidebar.text_input("Workup ticker", key="workup_ticker", placeholder="CW")
    st.sidebar.checkbox("vol overlay", key="vol")
    st.sidebar.checkbox("technical overlay", key="tv")
    st.sidebar.checkbox("prediction overlay", key="prediction")

    c = service.creds.status()
    st.sidebar.subheader("Credentials")
    st.sidebar.write(f"SEC user-agent: {'✓' if c.sec_user_agent['detected'] else '✗'} "
                     f"({c.sec_user_agent['origin']})")
    st.sidebar.write(f"tastytrade (vol): {'✓' if c.tastytrade['detected'] else '✗'}")
    st.sidebar.write(f"tradingview: {'✓' if c.tradingview['sessionid_detected'] else '✗'} "
                     f"· {c.tradingview['mode']}")
    v = c.valuation_defaults
    st.sidebar.caption(f"WACC {v['wacc']*100:.1f}% · g {v['terminal_growth']*100:.1f}% "
                       f"· {v['forecast_years']}y · {PRIMARY} primary {NONPRIMARY} non-primary")
    return page


def _tickers():
    raw = st.session_state.get("tickers", "")
    out = [t.strip().upper() for t in raw.replace(",", " ").split()]
    return out or None


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_universe():
    st.header("Universe")
    v = service.universe.status()
    if not v.built:
        st.info(v.instructions)
        return
    a, b = st.columns(2)
    a.metric("Names", v.count)
    b.metric("Sycamore-owned", v.sycamore_owned)
    st.subheader("Source breakdown")
    st.dataframe(pd.DataFrame(
        [{"source": k, "count": n} for k, n in v.source_breakdown.items()]), hide_index=True)
    download_fig(v.csv_artifact, "universe.csv")


def page_screener():
    st.header("Screener — three-attribute (downside-first)")
    tk, sector = _tickers(), (st.session_state.get("sector") or None)
    if not (tk or sector):
        st.info("Enter tickers or a sector in the sidebar. Fundamentals: SEC EDGAR "
                "(primary 🟢); prices/market cap: yfinance (non-primary ⚪).")
        return
    try:
        view = service.screener.run(tickers=tk, sector=sector,
                                    with_vol=st.session_state.get("vol", False),
                                    tv_overlay=st.session_state.get("tv", False),
                                    with_prediction_overlay=st.session_state.get("prediction", False))
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")
        return

    st.caption(f"Rank integrity — overlays never move composite_rank "
               f"({view.rank_unchanged.citation}).")
    if st.button("Prove it (re-runs the screen twice)"):
        with st.spinner("re-running overlays off vs on…"):
            try:
                proof = service.screener.verify_rank_unchanged(tickers=tk, sector=sector)
                (st.success if proof.identical else st.error)(
                    "composite_rank identical with overlays off vs on"
                    if proof.identical else "rank changed — investigate")
                st.dataframe(pd.DataFrame(proof.rows), hide_index=True)
            except Exception as exc:  # noqa: BLE001
                st.error(f"{type(exc).__name__}: {exc}")

    for k, n in view.notes.items():
        if n:
            st.warning(f"{k} overlay: {n}")

    rows = [{
        "Rank": r.composite_rank,
        "Ticker": r.ticker,
        "Name": r.name,
        "Q1 Quality": r.q1_quality.value if r.q1_quality else None,
        "Q2 Valuation": r.q2_valuation.value if r.q2_valuation else None,
        "Q3 Improving": r.q3_improving.value if r.q3_improving else None,
        "Negative space": ("⚠ " + ", ".join(r.ns_flags)) if r.ns_flags else "",
        "Market cap": r.market_cap.value if r.market_cap else None,
        "Sources": ", ".join(r.sources),
    } for r in view.rows]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        column_config={
            "Q1 Quality": st.column_config.ProgressColumn(
                "Q1 Quality", min_value=0, max_value=100, format="%.0f",
                help="EDGAR fundamentals (primary)"),
            "Q2 Valuation": st.column_config.ProgressColumn(
                "Q2 Valuation", min_value=0, max_value=100, format="%.0f",
                help="edgar + yfinance price (non-primary)"),
            "Q3 Improving": st.column_config.ProgressColumn(
                "Q3 Improving", min_value=0, max_value=100, format="%.0f",
                help="EDGAR fundamentals (primary)"),
            "Market cap": st.column_config.NumberColumn(help="yfinance (non-primary)"),
        },
    )
    st.caption("Q1 / Q2 / Q3 are three separate scores — composite_rank is only a sort key.")
    download_fig(view.screener_artifact, "screener_output.xlsx (full, auditable)")

    vol_show = [c for c in view.overlay_columns["vol"] if c not in ("vol_flags", "vol_source")]
    if vol_show or view.overlay_columns["tv"] or view.overlay_columns["prediction"]:
        with st.expander("🔒 NON-PRIMARY overlays — context only; walled off from the score & rank",
                         expanded=False):
            orows = []
            for r in view.rows:
                row = {"Ticker": r.ticker}
                for c in vol_show:
                    row[c] = (r.vol[c].value if (r.vol and c in r.vol and r.vol[c]) else None)
                for c in view.overlay_columns["tv"]:
                    row[c] = (r.tv or {}).get(c)
                for c in view.overlay_columns["prediction"]:
                    row[c] = (r.prediction or {}).get(c)
                orows.append(row)
            st.dataframe(pd.DataFrame(orows), hide_index=True)


def page_workup():
    st.header("Name workup")
    ticker = (st.session_state.get("workup_ticker") or "").upper()
    if not ticker:
        st.info("Enter a workup ticker in the sidebar.")
        return
    try:
        view = service.workup.for_ticker(ticker, with_vol=st.session_state.get("vol", False))
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")
        return

    bank = " · bank" if view.is_bank else ""
    st.subheader(f"{view.ticker} — {view.name or ''}{bank}")
    if view.error:
        st.warning(view.error)
    c1, c2, c3 = st.columns(3)
    metric(c1, "Base margin of safety", view.base_margin_of_safety, delta_risk=True)
    metric(c2, "Implied growth priced in", view.base_implied_growth)
    metric(c3, "Price", view.price)

    st.subheader("Reverse DCF — bear · base · bull")
    cols = st.columns(3)
    for col, case in zip(cols, view.dcf_cases):
        col.markdown(f"**{case.label.capitalize()}**")
        metric(col, "Fair value", case.fair_value_per_share)
        metric(col, "Margin of safety", case.margin_of_safety, delta_risk=True)
        col.caption(f"implied g {fig_text(case.implied_growth)}")

    if view.normalized:
        st.subheader("Normalized / trough earnings (downside anchor)")
        n = view.normalized
        st.dataframe(pd.DataFrame([{
            "Normalized EPS": fig_text(n.normalized_eps), "Normalized P/E": fig_text(n.normalized_pe),
            "Trough EPS": fig_text(n.trough_eps), "Trough P/E": fig_text(n.trough_pe),
            "Trailing P/E": fig_text(n.trailing_pe),
        }]), hide_index=True)

    if view.bands:
        st.subheader("Own-history valuation bands")
        st.dataframe(pd.DataFrame([{
            "Multiple": b.multiple, "Current": fig_text(b.current),
            "Cheap pctile": fig_text(b.percentile_cheap),
            "Median": fig_text(b.median), "Min": fig_text(b.p_min), "Max": fig_text(b.p_max),
            "n": b.n, "Direction": "lower = cheap" if b.lower_is_cheap else "higher = cheap",
        } for b in view.bands]), hide_index=True)

    if view.vol_panel:
        with st.expander("🔒 NON-PRIMARY volatility (option-implied; data-only)", expanded=False):
            if view.vol_panel.note:
                st.info(view.vol_panel.note)
            else:
                vp = view.vol_panel
                st.dataframe(pd.DataFrame([{
                    "IV rank": fig_text(vp.iv_rank), "IV pctile": fig_text(vp.iv_percentile),
                    "IV index": fig_text(vp.iv_index), "Exp move 30d": fig_text(vp.expected_move_30d_pct),
                    "σ-down price": fig_text(vp.sigma_down_30d_price),
                    "Flags": ", ".join(vp.vol_flags) or "—",
                }]), hide_index=True)

    st.subheader("Downloads (auditable)")
    download_fig(view.comps_artifacts.get("xlsx"), "comps.xlsx")
    download_fig(view.comps_artifacts.get("md"), "comps tear-sheet.md")
    if view.model_artifact:
        download_fig(view.model_artifact, "Excel model (download to flex live)")
    else:
        st.caption("Excel model not built yet — building the scaffold is a Phase B action; "
                   "the UI never reimplements the live formulas.")


def page_pipeline():
    st.header("Pipeline runs (read-only)")
    runs = service.pipeline.list_runs()
    if not runs:
        st.info("No pipeline runs in cache. Running the funnel is a background job (Phase C); "
                "this view renders existing pipeline_<ts>/ folders.")
        return
    names = [r.name for r in runs]
    chosen = st.selectbox("Run", names)
    token = next(r.run_token for r in runs if r.name == chosen)
    try:
        view = service.pipeline.load(token)
    except (KeyError, PermissionError) as exc:
        st.error(f"unknown run: {exc}")
        return
    st.caption(f"generated {view.generated} · sources: {view.sources}")
    c1, c2, c3 = st.columns(3)
    with c1:
        download_fig(view.index_artifacts.get("xlsx"), "index.xlsx")
    with c2:
        download_fig(view.index_artifacts.get("md"), "index.md")
    with c3:
        download_fig(view.manifest_artifact, "manifest.json")

    st.subheader("Ranked dossiers (downside-first)")
    st.dataframe(pd.DataFrame([{
        "Rank": d.composite_rank, "Ticker": d.ticker, "Name": d.name,
        "Q1": d.q1_quality.value if d.q1_quality else None,
        "Q2": d.q2_valuation.value if d.q2_valuation else None,
        "Q3": d.q3_improving.value if d.q3_improving else None,
        "MoS (base)": fig_text(d.margin_of_safety_base),
        "Implied g": fig_text(d.implied_growth_base),
        "Trough P/E": fig_text(d.trough_pe),
        "Flags": ("⚠ " + ", ".join(d.ns_flags)) if d.ns_flags else "",
    } for d in view.dossiers]), hide_index=True)
    with st.expander("Run manifest (reproducibility)"):
        st.json(view.manifest)


def page_spinoffs():
    st.header("Spin-offs (read-only)")
    v = service.spinoffs.cached()
    if v.note:
        st.info(v.note)
    for a in v.artifacts:
        download_fig(a, a.filename)


def page_overlays():
    st.header("Overlays — NON-PRIMARY, walled off from scoring")
    m = service.prediction.mapping_view()
    st.markdown("**Prediction-market mapping (Polymarket)** — never feeds the score or any fair value.")
    if m.note:
        st.info(m.note)
    st.caption(f"Source CSV: {m.path_note} · confirmed {m.counts.get('confirmed', 0)} · "
               f"annotated {m.counts.get('annotated', 0)} · machine-owned {m.counts.get('machine_owned', 0)}")
    if m.rows:
        st.dataframe(pd.DataFrame([{
            "Confirmed": r.confirmed, "Ticker": r.ticker, "Aperture": r.aperture,
            "Question": r.question, "Event": r.event_type, "Direction": r.direction,
            "Relevance": r.relevance_score.value if r.relevance_score else None,
            "Notes": r.notes, "Human-owned": r.human_owned,
        } for r in m.rows]), hide_index=True,
            column_config={"Confirmed": st.column_config.CheckboxColumn(disabled=True)})
    st.caption("Phase A is read-only — the confirm checkboxes are display-only. "
               "The write-back confirm table is wired in Phase B.")


PAGES = {
    "Universe": page_universe,
    "Screener": page_screener,
    "Name workup": page_workup,
    "Pipeline": page_pipeline,
    "Spin-offs": page_spinoffs,
    "Overlays": page_overlays,
}


def main():
    PAGES[sidebar()]()


main()

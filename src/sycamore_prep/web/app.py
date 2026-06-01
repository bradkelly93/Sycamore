"""FastAPI routes for the read-only Explorer. Each route calls the service layer
and renders a view-model; no analytics live here.

Phase A is READ-ONLY: the screener/workup pulls (cached, idempotent) are the
Explorer's core reads, but there are no build / scan / pipeline-run / universe-build
actions (those are Phase B/C). Every networked read degrades to the engine's skip
note (surfaced in the view), never a traceback.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import service
from .format import fmt_value

app = FastAPI(title="Sycamore Explorer (read-only)")

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_TEMPLATES.env.filters["fmt_value"] = fmt_value


def _ctx(**kw):
    """Common context — creds/controls live in the sidebar on every page."""
    base = {"creds": service.creds.status(), "active": kw.pop("active", "")}
    base.update(kw)
    return base


def _render(request: Request, name: str, **kw):
    return _TEMPLATES.TemplateResponse(request, name, _ctx(**kw))


def _parse_tickers(tickers: str | None) -> list[str] | None:
    if not tickers:
        return None
    out = [t.strip().upper() for t in tickers.replace(",", " ").split()]
    return out or None


@app.get("/", response_class=HTMLResponse)
@app.get("/universe", response_class=HTMLResponse)
def universe(request: Request):
    return _render(request, "universe.html", active="universe", view=service.universe.status())


@app.get("/screener", response_class=HTMLResponse)
def screener(request: Request, tickers: str | None = None, sector: str | None = None,
             limit: int | None = None, vol: bool = False, tv: bool = False,
             prediction: bool = False):
    view = err = None
    tk = _parse_tickers(tickers)
    if tk or sector:
        try:
            view = service.screener.run(tickers=tk, sector=sector, limit=limit,
                                        with_vol=vol, tv_overlay=tv,
                                        with_prediction_overlay=prediction)
        except Exception as exc:  # noqa: BLE001 — surface as a note, never a 500
            err = f"{type(exc).__name__}: {exc}"
    return _render(request, "screener.html", active="screener", view=view, err=err,
                   q={"tickers": tickers or "", "sector": sector or "",
                      "vol": vol, "tv": tv, "prediction": prediction})


@app.post("/screener/verify", response_class=HTMLResponse)
def screener_verify(request: Request, tickers: str | None = None, sector: str | None = None,
                    limit: int | None = None):
    proof = err = None
    try:
        proof = service.screener.verify_rank_unchanged(
            tickers=_parse_tickers(tickers), sector=sector, limit=limit)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    return _TEMPLATES.TemplateResponse(request, "_rank_proof.html", {"proof": proof, "err": err})


@app.get("/workup/{ticker}", response_class=HTMLResponse)
def workup(request: Request, ticker: str, vol: bool = False):
    view = err = None
    try:
        view = service.workup.for_ticker(ticker.upper(), with_vol=vol)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    return _render(request, "workup.html", active="workup", view=view, err=err,
                   ticker=ticker.upper(), vol=vol)


@app.get("/workup/{ticker}/news", response_class=HTMLResponse)
def workup_news(request: Request, ticker: str):
    """Lazy-loaded NON-PRIMARY headlines panel (HTMX loads it after the page so a
    slow/offline news feed never blocks the deep-dive)."""
    return _TEMPLATES.TemplateResponse(
        request, "_news.html", {"news": service.news.for_ticker(ticker.upper())})


@app.get("/pipeline", response_class=HTMLResponse)
@app.get("/pipeline/{run_token}", response_class=HTMLResponse)
def pipeline(request: Request, run_token: str | None = None):
    runs = service.pipeline.list_runs()
    view = err = None
    try:
        if run_token:
            view = service.pipeline.load(run_token)
        elif runs:
            view = service.pipeline.load(runs[0].run_token)
    except (KeyError, PermissionError) as exc:
        err = f"unknown run: {exc}"
    return _render(request, "pipeline.html", active="pipeline", runs=runs, view=view, err=err)


@app.get("/spinoffs", response_class=HTMLResponse)
def spinoffs(request: Request):
    return _render(request, "spinoffs.html", active="spinoffs", view=service.spinoffs.cached())


@app.get("/overlays", response_class=HTMLResponse)
def overlays(request: Request):
    return _render(request, "overlays.html", active="overlays",
                   mapping=service.prediction.mapping_view(editable=True))


@app.get("/download/{token}")
def download(token: str):
    """Serve a whitelisted artifact by token (404 on forged/out-of-whitelist)."""
    try:
        path = service.artifacts.resolve(token)
    except (KeyError, PermissionError):
        return HTMLResponse("Not found", status_code=404)
    if not path.exists():
        return HTMLResponse("Not found", status_code=404)
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, filename=path.name, media_type=media)


# --------------------------------------------------------------------------- #
# Phase B — fast, inline single-name actions (HTMX posts; swap a result card)
# --------------------------------------------------------------------------- #
def _action_card(request: Request, result):
    return _TEMPLATES.TemplateResponse(request, "_action_result.html", {"r": result})


@app.post("/action/build-model", response_class=HTMLResponse)
def action_build_model(request: Request, ticker: str = Form(...)):
    return _action_card(request, service.actions.build_model(ticker))


@app.post("/action/vol", response_class=HTMLResponse)
def action_vol(request: Request, ticker: str = Form(...)):
    return _action_card(request, service.actions.run_vol(ticker))


@app.post("/action/track-spinoff", response_class=HTMLResponse)
def action_track_spinoff(request: Request, parent: str = Form(...),
                         spinco: str = Form(None)):
    return _action_card(request, service.actions.track_spinoff(parent, spinco or None))


@app.post("/prediction/confirm", response_class=HTMLResponse)
def prediction_confirm(request: Request, ticker: str = Form(...), slug: str = Form(...),
                       confirmed: bool = Form(False), notes: str = Form("")):
    """Write one human edit back to the mapping CSV (preserves edits by design),
    then re-render the mapping table partial."""
    try:
        mapping = service.prediction.confirm(
            [{"ticker": ticker, "slug": slug, "confirmed": confirmed, "notes": notes}])
    except Exception as exc:  # noqa: BLE001
        mapping = service.prediction.mapping_view(editable=True)
        mapping.note = f"Save failed: {type(exc).__name__}: {exc}"
    return _TEMPLATES.TemplateResponse(request, "_mapping_table.html", {"mapping": mapping})


# --------------------------------------------------------------------------- #
# Phase C — heavy background jobs (submit -> poll a status partial)
# --------------------------------------------------------------------------- #
@app.post("/jobs/rebuild-universe", response_class=HTMLResponse)
def job_rebuild_universe(request: Request):
    job_id = service.jobs.submit("rebuild-universe", service.actions.rebuild_universe_job)
    return _job_partial(request, job_id)


@app.post("/jobs/run-pipeline", response_class=HTMLResponse)
def job_run_pipeline(request: Request, tickers: str = Form(None), sector: str = Form(None),
                     sycamore_only: bool = Form(False), top: int = Form(10)):
    job_id = service.jobs.submit(
        "run-pipeline", service.actions.run_pipeline_job,
        tickers=_parse_tickers(tickers), sector=(sector or None),
        sycamore_only=sycamore_only, top=top)
    return _job_partial(request, job_id)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    return _job_partial(request, job_id)


def _job_partial(request: Request, job_id: str):
    job = service.jobs.get(job_id)
    return _TEMPLATES.TemplateResponse(request, "_job.html", {"job": job})

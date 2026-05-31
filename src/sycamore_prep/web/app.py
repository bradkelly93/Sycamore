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

from fastapi import FastAPI, Request
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
                   mapping=service.prediction.mapping_view())


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

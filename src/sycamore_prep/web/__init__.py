"""FastAPI + Jinja2/HTMX read-only Explorer (Phase A bake-off).

A thin server-rendered lens over ``sycamore_prep.service``. No analytics here —
routes call the service and templates render the view-models. Launch:

    uvicorn sycamore_prep.web:app
"""

from __future__ import annotations

from .app import app

__all__ = ["app"]

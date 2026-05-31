"""Tiny in-process background-job runner (Phase C). Built + unit-tested now, but
NOT wired into any UI until Phase C.

Design (UI_SPEC §5/§7): a single-worker ``ThreadPoolExecutor`` — work is I/O-bound
(SEC/yfinance HTTP), so threads keep the UI responsive; ``max_workers=1`` serializes
runs (respects the SEC ~10 rps limit and avoids cache contention). The registry is a
module-level singleton so it survives Streamlit reruns (never ``st.session_state``).
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .viewmodels import ArtifactRef, JobView

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sycamore-job")
_REGISTRY: dict[str, "Job"] = {}
_LOCK = threading.Lock()


@dataclass
class Job:
    id: str
    kind: str
    state: str = "queued"          # queued | running | done | error
    progress: float | None = None
    note: str | None = None
    started: float | None = None
    finished: float | None = None
    result: Any = None
    error: str | None = None


def _run(job: Job, fn: Callable, args: tuple, kwargs: dict) -> None:
    with _LOCK:
        job.state = "running"
        job.started = time.time()
    try:
        result = fn(*args, **kwargs)
        with _LOCK:
            job.result = result
            job.state = "done"
    except Exception as exc:  # noqa: BLE001 — surface as job.error, never crash the worker
        with _LOCK:
            job.error = f"{type(exc).__name__}: {exc}"
            job.state = "error"
    finally:
        with _LOCK:
            job.finished = time.time()


def submit(kind: str, fn: Callable, *args, **kwargs) -> str:
    """Queue ``fn(*args, **kwargs)`` on the worker; returns a poll-able job id."""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    with _LOCK:
        _REGISTRY[job.id] = job
    _EXECUTOR.submit(_run, job, fn, args, kwargs)
    return job.id


def _result_ref(result: Any) -> ArtifactRef | None:
    return result if isinstance(result, ArtifactRef) else None


def get(job_id: str) -> JobView | None:
    with _LOCK:
        job = _REGISTRY.get(job_id)
        if job is None:
            return None
        elapsed = None
        if job.started is not None:
            end = job.finished if job.finished is not None else time.time()
            elapsed = round(end - job.started, 3)
        return JobView(
            id=job.id, kind=job.kind, state=job.state, progress=job.progress, note=job.note,
            started=datetime.fromtimestamp(job.started).isoformat() if job.started else None,
            finished=datetime.fromtimestamp(job.finished).isoformat() if job.finished else None,
            elapsed_s=elapsed, result_ref=_result_ref(job.result), error=job.error,
        )


def list_jobs() -> list[JobView]:
    with _LOCK:
        ids = list(_REGISTRY)
    return [v for v in (get(i) for i in ids) if v is not None]

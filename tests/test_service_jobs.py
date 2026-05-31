"""The Phase-C background-job runner (built + tested now, unused until C)."""

from __future__ import annotations

import time

from sycamore_prep.service import jobs
from sycamore_prep.service.viewmodels import ArtifactRef


def _wait(job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = jobs.get(job_id)
        if v and v.state in ("done", "error"):
            return v
        time.sleep(0.02)
    return jobs.get(job_id)


def test_job_runs_to_done():
    jid = jobs.submit("test", lambda x: x + 1, 41)
    v = _wait(jid)
    assert v.state == "done"
    assert v.elapsed_s is not None and v.error is None


def test_job_error_captured():
    def boom():
        raise RuntimeError("kaboom")
    v = _wait(jobs.submit("test", boom))
    assert v.state == "error"
    assert "kaboom" in v.error


def test_job_result_ref_when_artifact():
    ref = ArtifactRef(kind="xlsx", filename="x.xlsx", token="tok")
    v = _wait(jobs.submit("test", lambda: ref))
    assert v.state == "done"
    assert v.result_ref is not None and v.result_ref.filename == "x.xlsx"


def test_unknown_job_returns_none():
    assert jobs.get("nope-not-a-job") is None

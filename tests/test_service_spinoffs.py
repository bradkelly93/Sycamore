"""SpinoffsView: read-only listing of cached scan/track artifacts."""

from __future__ import annotations

from sycamore_prep.service import spinoffs as svc
from sycamore_prep.service.viewmodels import ArtifactRef


def test_cached_lists_artifacts(wl_root):
    (wl_root.cache / "spinoffs_scan.xlsx").write_text("x")
    (wl_root.cache / "spinoffs_GE.md").write_text("x")
    view = svc.cached()
    assert len(view.artifacts) == 2
    assert all(isinstance(a, ArtifactRef) for a in view.artifacts)
    assert view.note is None


def test_cached_empty_has_phase_b_note(wl_root):
    view = svc.cached()
    assert view.artifacts == []
    assert view.note and "Phase B" in view.note

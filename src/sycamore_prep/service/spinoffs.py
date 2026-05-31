"""Service wrapper for the spin-off tracker — READ-ONLY in Phase A.

Lists cached ``spinoffs_*`` scan/track outputs as downloadable artifacts. Running
a live scan/track is a network action wired in Phase B (``tracker.run_scan`` /
``run_track``); this module never pulls.
"""

from __future__ import annotations

from ..config import cache_dir
from . import artifacts
from .viewmodels import SpinoffsView


def cached() -> SpinoffsView:
    cd = cache_dir()
    files = sorted(set(cd.glob("spinoffs_*.xlsx")) | set(cd.glob("spinoffs_*.md")))
    refs = [artifacts.register(f) for f in files if f.is_file()]
    note = None
    if not refs:
        note = ("No cached spin-off scans. Live scan/track (10-12B / Form 10) is a "
                "network action wired in Phase B; this read-only view lists cached results.")
    return SpinoffsView(artifacts=[r for r in refs if r is not None], note=note)

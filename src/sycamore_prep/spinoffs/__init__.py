"""Spin-off tracker (Phase 4): discovery + three-attribute / value-trap flags.

Special-situations value source — forced index selling on the orphaned SpinCo
creates mispricing — but a value trap when the parent dumps debt/pension/
litigation. Downside-first, three-attribute, primary-source, auditable.
"""

from .discovery import (
    SpinoffRecord,
    Status,
    scan_recent_form10s,
    status_from_submissions,
    track_parent,
)
from .flags import DOWNSIDE_THRESHOLDS, apply_flags
from .tracker import TrackerResult, load_tracker, run_scan, run_track

__all__ = [
    "SpinoffRecord",
    "Status",
    "scan_recent_form10s",
    "track_parent",
    "status_from_submissions",
    "apply_flags",
    "DOWNSIDE_THRESHOLDS",
    "TrackerResult",
    "run_scan",
    "run_track",
    "load_tracker",
]

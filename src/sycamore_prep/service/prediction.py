"""Service wrapper for the NON-PRIMARY prediction-market mapping.

Phase A: ``mapping_view`` renders the editable CSV read-only (the checkbox table
the UI will later make writable). Phase B: ``confirm`` writes human edits back via
``mapping.save_mapping``, preserving the machine/human ownership invariant
(``_is_machine_owned`` at mapping.py:84) — these functions exist now and are unit-
tested, but the Phase-A UI never calls ``confirm``.
"""

from __future__ import annotations

from ..config import mapping_csv_path
from ..prediction_markets.mapping import _blank, load_mapping, save_mapping
from .serde import clean_scalar
from .viewmodels import POLYMARKET, MappingRowView, MappingView, fig


def _row_view(row: dict) -> MappingRowView:
    confirmed = bool(row.get("confirmed"))
    notes = "" if _blank(row.get("notes")) else str(row.get("notes"))
    return MappingRowView(
        ticker=str(clean_scalar(row.get("ticker")) or ""),
        aperture=clean_scalar(row.get("aperture")),
        slug=clean_scalar(row.get("slug")),
        question=clean_scalar(row.get("question")),
        event_type=clean_scalar(row.get("event_type")),
        direction=clean_scalar(row.get("direction")),
        relevance_score=fig(row.get("relevance_score"), POLYMARKET),
        confirmed=confirmed,
        notes=notes,
        human_owned=confirmed or bool(notes.strip()),
    )


def mapping_view(mapping_path=None, *, editable: bool = False) -> MappingView:
    df = load_mapping(mapping_path)
    rows = [_row_view(r) for r in df.to_dict(orient="records")]
    confirmed = sum(1 for r in rows if r.confirmed)
    annotated = sum(1 for r in rows if r.notes.strip())
    machine_owned = sum(1 for r in rows if not r.human_owned)
    note = None
    if not rows:
        note = ("No prediction-market mappings yet. Run prediction discovery to "
                "propose candidates (Phase B), then confirm the relevant ones here.")
    return MappingView(
        rows=rows,
        path_note=str(mapping_csv_path() if mapping_path is None else mapping_path),
        editable=editable,
        counts={"confirmed": confirmed, "machine_owned": machine_owned, "annotated": annotated},
        note=note,
    )


def confirm(updates: list[dict], mapping_path=None) -> MappingView:
    """Apply human edits (confirmed / notes / event_type / direction) to existing
    rows keyed by (ticker, slug) and persist. Phase B write-back path."""
    df = load_mapping(mapping_path)
    for u in updates:
        tk = str(u.get("ticker", ""))
        slug = str(u.get("slug", ""))
        mask = (df["ticker"].astype(str) == tk) & (df["slug"].astype(str) == slug)
        for field_name in ("confirmed", "notes", "event_type", "direction"):
            if field_name in u:
                df.loc[mask, field_name] = u[field_name]
    save_mapping(df, mapping_path)
    return mapping_view(mapping_path, editable=True)

"""Read/write the editable ticker→market mapping CSV (data/raw/).

This is the human-in-the-loop source of truth. `prediction-discover` proposes
rows with a machine `relevance_score`; the analyst edits `confirmed`,
`event_type`, `direction`, and `notes`. Re-running discovery UPSERTS — it
refreshes the machine fields (relevance_score, question) and adds new
candidates, but never overwrites a human's edits or deletes a confirmed row
(CLAUDE.md: auditable, analyst tooling not a black box).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import mapping_csv_path


MAPPING_COLUMNS = [
    "ticker", "aperture", "slug", "question",
    "event_type", "direction", "relevance_score", "confirmed", "notes",
]


def empty_mapping() -> pd.DataFrame:
    return pd.DataFrame(columns=MAPPING_COLUMNS)


def _coerce_bool(s: pd.Series) -> pd.Series:
    def one(v: object) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        try:
            if pd.isna(v):
                return False
        except (TypeError, ValueError):
            pass
        return str(v).strip().lower() in ("true", "1", "yes", "y", "t")
    return s.map(one)


def _blank(v: object) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() == ""


def _with_defaults(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in MAPPING_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df["confirmed"] = _coerce_bool(df["confirmed"])
    df["notes"] = df["notes"].fillna("")
    return df[MAPPING_COLUMNS]


def load_mapping(path: Path | str | None = None) -> pd.DataFrame:
    p = Path(path) if path else mapping_csv_path()
    if not p.exists():
        return empty_mapping()
    return _with_defaults(pd.read_csv(p))


def save_mapping(df: pd.DataFrame, path: Path | str | None = None) -> Path:
    p = Path(path) if path else mapping_csv_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = _with_defaults(df).sort_values(
        ["ticker", "aperture", "relevance_score"],
        ascending=[True, True, False], na_position="last",
    )
    out.to_csv(p, index=False)
    return p


def upsert(existing: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Merge discovery candidates into the mapping on (ticker, slug).

    Human-owned fields (confirmed, notes, and any non-blank event_type/
    direction/aperture) are preserved; the machine fields relevance_score and
    question are refreshed. New candidates are appended as confirmed=False.
    """
    base = (
        _with_defaults(existing)
        if existing is not None and not existing.empty
        else empty_mapping()
    )
    records: dict[tuple[str, str], dict] = {
        (str(r["ticker"]), str(r["slug"])): r.to_dict()
        for _, r in base.iterrows()
    }
    if candidates is not None and not candidates.empty:
        for _, c in _with_defaults(candidates).iterrows():
            key = (str(c["ticker"]), str(c["slug"]))
            if key in records:
                row = records[key]
                row["relevance_score"] = c["relevance_score"]
                row["question"] = c["question"]
                # Fill machine guesses only where the human left a blank.
                for f in ("aperture", "event_type", "direction"):
                    if _blank(row.get(f)):
                        row[f] = c.get(f)
            else:
                row = c.to_dict()
                row["confirmed"] = False  # new candidates start unconfirmed
                records[key] = row
    return pd.DataFrame(list(records.values()))[MAPPING_COLUMNS]

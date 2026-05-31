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


def _is_machine_owned(row: dict) -> bool:
    """True if a row is an untouched machine proposal — unconfirmed AND un-noted.
    A human who cares about a row either confirms it or writes a note, so these
    two flags are the safe signal that a row can be pruned on re-discovery.
    (direction can't be the signal — the machine itself guesses risk/opportunity.)
    """
    return (not bool(row.get("confirmed"))) and _blank(row.get("notes"))


def upsert(existing: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Merge discovery candidates into the mapping on (ticker, slug).

    Human-owned fields (confirmed, notes, and any non-blank event_type/
    direction/aperture) are preserved; the machine fields relevance_score and
    question are refreshed. New candidates are appended as confirmed=False.

    Self-pruning: when a (ticker, aperture) pair is re-discovered, existing
    rows in that pair that are machine-owned (unconfirmed + un-noted) and NO
    LONGER in the fresh candidate set are dropped — so a re-run clears stale
    junk (e.g. near-dated markets the new time-to-resolution filter now
    excludes) without ever touching a confirmed or annotated row.
    """
    base = (
        _with_defaults(existing)
        if existing is not None and not existing.empty
        else empty_mapping()
    )
    cand = _with_defaults(candidates) if candidates is not None and not candidates.empty \
        else empty_mapping()

    # (ticker, aperture) pairs that this discovery run actually refreshed, and
    # the (ticker, slug) keys it returned — used to prune stale machine rows.
    refreshed_pairs = {(str(c["ticker"]), str(c["aperture"])) for _, c in cand.iterrows()}
    cand_keys = {(str(c["ticker"]), str(c["slug"])) for _, c in cand.iterrows()}

    records: dict[tuple[str, str], dict] = {}
    for _, r in base.iterrows():
        row = r.to_dict()
        key = (str(r["ticker"]), str(r["slug"]))
        pair = (str(r["ticker"]), str(r["aperture"]))
        # Drop a stale machine-owned row only when its pair was re-discovered
        # AND it's absent from the fresh candidates. Confirmed/annotated rows
        # and untouched pairs are always kept.
        if pair in refreshed_pairs and key not in cand_keys and _is_machine_owned(row):
            continue
        records[key] = row

    for _, c in cand.iterrows():
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

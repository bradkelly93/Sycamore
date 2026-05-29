"""Fail-safe extraction of a spin-off's soft fields from the 10-12B information
statement: distribution ratio + record / distribution dates.

Happy medium (per the approved plan): a NARROW, source-tagged pass over the
filing text that NEVER guesses — on no match, or an ambiguous / conflicting
match, it returns ``None`` so the field renders "pending — see Form 10 [link]".
Anything extracted is tagged ``derived (parsed 10-12B)`` and shown next to its
filing link for one-click audit.

The regexes are intentionally conservative and are calibrated against real
information-statement phrasing captured by scripts/precheck_infostmt.py
(lesson #1: look, don't guess). When the text is missing or doesn't match a
known pattern, the analyst still gets the filing link to read it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

SOURCE_TAG = "derived (parsed 10-12B)"

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_MONTH = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
_DATE_RE = re.compile(rf"((?:{_MONTH})\s+\d{{1,2}},\s+\d{{4}})", re.I)

# "... one share of SpinCo ... for every two shares of Parent ..."
_RATIO_FOR_EVERY = re.compile(
    r"(?P<num>one|two|three|\d+)\s+share[s]?\b.{0,120}?\bfor\s+every\s+"
    r"(?P<den>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+share",
    re.I,
)
# "at a distribution ratio of 1 to 2" / "ratio of 1:2"
_RATIO_OF = re.compile(
    r"ratio\s+of\s+(?P<num>\d+(?:\.\d+)?)\s*(?:to|:|-)\s*(?P<den>\d+(?:\.\d+)?)",
    re.I,
)


@dataclass
class SoftFields:
    distribution_ratio: str | None = None      # e.g. "1:2"
    record_date: str | None = None             # ISO YYYY-MM-DD when parseable
    distribution_date: str | None = None
    source: str | None = None                  # SOURCE_TAG iff anything extracted


def _normalize(text: str) -> str:
    # Strip tags if HTML slipped through, collapse whitespace.
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _num(token: str) -> float | None:
    token = token.strip().lower()
    if token in _WORD_NUM:
        return float(_WORD_NUM[token])
    try:
        return float(token)
    except ValueError:
        return None


def _ratio(text: str) -> str | None:
    """Return 'num:den' if exactly one consistent ratio is found, else None."""
    found: set[str] = set()
    for m in _RATIO_FOR_EVERY.finditer(text):
        num, den = _num(m.group("num")), _num(m.group("den"))
        if num and den:
            found.add(f"{num:g}:{den:g}")
    for m in _RATIO_OF.finditer(text):
        num, den = _num(m.group("num")), _num(m.group("den"))
        if num and den:
            found.add(f"{num:g}:{den:g}")
    return found.pop() if len(found) == 1 else None  # ambiguous/none -> pending


def _iso(date_str: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return date_str.strip()


def _date_near(text: str, anchors: list[str], window: int = 160) -> str | None:
    """First date within `window` chars after any anchor phrase; else None."""
    for anchor in anchors:
        a = re.search(re.escape(anchor), text, re.I)
        if not a:
            continue
        seg = text[a.end(): a.end() + window]
        d = _DATE_RE.search(seg)
        if d:
            return _iso(d.group(1))
    return None


def extract(text: str | None) -> SoftFields:
    if not text:
        return SoftFields()
    t = _normalize(text)
    ratio = _ratio(t)
    rec = _date_near(t, ["record date"])
    dist = _date_near(t, ["distribution date", "distribution is expected", "to be completed on"])
    src = SOURCE_TAG if (ratio or rec or dist) else None
    return SoftFields(distribution_ratio=ratio, record_date=rec, distribution_date=dist, source=src)

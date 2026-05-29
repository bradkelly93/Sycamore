"""Fail-safe extraction of a spin-off's soft fields from the 10-12B information
statement: distribution ratio + record / distribution dates.

Happy medium (per the approved plan): a NARROW, source-tagged pass over the
filing text that NEVER guesses — on no match, or an ambiguous / conflicting
match, it returns ``None`` so the field renders "pending — see Form 10 [link]".
Anything extracted is tagged ``derived (parsed 10-12B)`` and shown next to its
filing link for one-click audit.

Patterns are calibrated to the real information-statement phrasing captured by
scripts/precheck_infostmt.py for Danaher->Veralto and 3M->Solventum, e.g.:

    "... one share of Veralto common stock for every three shares of Danaher
     common stock held at the close of business on September 13, 2023, the
     record date for the distribution. ... will be distributed by Danaher on
     September 30, 2023 ..."

Note the date often *precedes* the "record date" anchor, and the distribution
date hangs off "will be distributed / distribution will occur ... on <date>".
The terms live in the EX-99.1 information statement, not the 10-12B cover — the
tracker resolves that document before calling here.
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
_NUM = r"one|two|three|four|five|six|seven|eight|nine|ten|\d+"
_MONTH = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December")
_DATE = rf"(?:{_MONTH})\s+\d{{1,2}},\s+\d{{4}}"
_DATE_RE = re.compile(rf"({_DATE})", re.I)

# "... one share of SpinCo common stock for every three shares of Parent ..."
_RATIO_FOR_EVERY = re.compile(
    rf"(?P<num>{_NUM})\s+share[s]?\b.{{0,120}}?\bfor\s+every\s+(?P<den>{_NUM})\s+share",
    re.I,
)
# "... at a ratio of 1 to 2 ..." (digit form)
_RATIO_OF = re.compile(
    r"ratio\s+of\s+(?P<num>\d+(?:\.\d+)?)\s*(?:to|:|-)\s*(?P<den>\d+(?:\.\d+)?)",
    re.I,
)

# Date patterns. The date frequently PRECEDES the "record date" anchor.
_RECORD_PATTERNS = [
    re.compile(rf"({_DATE})\s*,?\s+(?:which is\s+)?the\s+record date", re.I),
    re.compile(rf"record date(?:\s+for the distribution)?\s+(?:is|will be|:)\s+"
               rf"(?:the close of business on\s+)?({_DATE})", re.I),
]
_DIST_PATTERNS = [
    re.compile(rf"(?:will be distributed|distribution will occur|"
               rf"distribution will be completed|"
               rf"distribution is expected to (?:occur|be completed))"
               rf".{{0,90}}?\bon\s+({_DATE})", re.I),
    re.compile(rf"distribution date.{{0,40}}?(?:is|will be|:)\s+({_DATE})", re.I),
]


@dataclass
class SoftFields:
    distribution_ratio: str | None = None      # e.g. "1:3"
    record_date: str | None = None             # ISO YYYY-MM-DD when parseable
    distribution_date: str | None = None
    source: str | None = None                  # SOURCE_TAG iff anything extracted


def _normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)        # strip tags if HTML slipped in
    text = text.replace(" ", " ")          # non-breaking spaces -> space
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
    """Return 'num:den' iff exactly one consistent ratio is found, else None."""
    found: set[str] = set()
    for pat in (_RATIO_FOR_EVERY, _RATIO_OF):
        for m in pat.finditer(text):
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


def _first_date(text: str, patterns: list[re.Pattern]) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return _iso(m.group(1))
    return None


def extract(text: str | None) -> SoftFields:
    if not text:
        return SoftFields()
    t = _normalize(text)
    ratio = _ratio(t)
    rec = _first_date(t, _RECORD_PATTERNS)
    dist = _first_date(t, _DIST_PATTERNS)
    src = SOURCE_TAG if (ratio or rec or dist) else None
    return SoftFields(distribution_ratio=ratio, record_date=rec, distribution_date=dist, source=src)

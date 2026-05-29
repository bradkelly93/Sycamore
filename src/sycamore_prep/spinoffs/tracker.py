"""Spin-off tracker orchestrator: discovery -> flags -> soft fields -> outputs.

Mirrors screener.run_screener / comps.run_comps. Two modes:

- ``run_scan``  — broad, fast market-wide discovery (metadata + status only;
  no per-SpinCo financials or document fetch, to respect SEC rate limits).
- ``run_track`` — deep-dive on a parent's spin: pull SpinCo financials -> the
  three-attribute / downside flags, parse the 10-12B for soft fields, and add
  the qualitative "review" prompts + the forced-selling window.

Downside-first, three attributes kept separate, every value source-tagged and
traceable to a filing. Missing data renders "pending", never a silent zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..adapters import EdgarProvider
from ..adapters import cache
from ..config import cache_dir, load_config
from . import discovery, softfields
from .discovery import SpinoffRecord
from .flags import apply_flags
from .report import records_to_df, write_tracker_md, write_tracker_xlsx

# Always-on analyst prompts: these can't be machine-read reliably, so the
# tracker points the analyst at the Form 10 rather than guessing.
REVIEW_PROMPTS = [
    "why_spun (read Form 10 information statement)",
    "pension / litigation / debt transfer to SpinCo (read Form 10)",
]


@dataclass
class TrackerResult:
    records: list[SpinoffRecord]
    df: pd.DataFrame
    mode: str
    xlsx_path: Path | None = None
    md_path: Path | None = None
    extra: dict = field(default_factory=dict)


def _set_forced_selling(rec: SpinoffRecord) -> None:
    """Flag the index forced-selling window around the distribution date."""
    if not rec.distribution_date:
        return
    try:
        dd = date.fromisoformat(rec.distribution_date)
    except ValueError:
        return
    days_since = (date.today() - dd).days
    if -30 <= days_since <= 120 and "forced_selling_window" not in rec.downside_flags:
        rec.downside_flags.append("forced_selling_window")


def _enrich(edgar: EdgarProvider, rec: SpinoffRecord, *, fetch_softfields: bool,
            use_cache: bool) -> SpinoffRecord:
    # 1) SpinCo fundamentals -> three-attribute + downside flags. Only when the
    #    SpinCo trades (has a ticker); a pre-trade SpinCo has no XBRL -> pending.
    ff = None
    if rec.spinco_ticker:
        try:
            ff = edgar.get_financials(rec.spinco_ticker, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001
            rec.error = (f"{rec.error}; " if rec.error else "") + f"financials: {exc}"
    apply_flags(rec, ff)

    # 2) Qualitative attributes that need the Form 10 / a market price.
    rec.review_flags = list(REVIEW_PROMPTS)
    rec.q2_valuation_disparity = (
        f"trades as {rec.spinco_ticker}; run `comps {rec.spinco_ticker}` for EV/EBITDA vs peers"
        if rec.spinco_ticker else "pending (no market price yet)"
    )

    # 3) Soft fields from the latest 10-12B (fail-safe to pending).
    if fetch_softfields and rec.filing_urls:
        try:
            sf = softfields.extract(edgar.get_filing_text(rec.filing_urls[-1], use_cache=use_cache))
            if sf.distribution_ratio:
                rec.distribution_ratio = sf.distribution_ratio
                rec.distribution_ratio_source = sf.source
            rec.record_date = sf.record_date or rec.record_date
            rec.distribution_date = sf.distribution_date or rec.distribution_date
        except Exception as exc:  # noqa: BLE001
            rec.error = (f"{rec.error}; " if rec.error else "") + f"softfields: {exc}"

    _set_forced_selling(rec)

    # 4) Source tags (primary + any derived parse).
    tags = ["edgar (primary)"]
    if rec.distribution_ratio_source:
        tags.append(rec.distribution_ratio_source)
    rec.sources = ", ".join(dict.fromkeys(tags))
    return rec


def _write(result: TrackerResult, output_path, default_stem: str) -> None:
    xlsx = Path(output_path) if output_path else (cache_dir() / f"{default_stem}.xlsx")
    md = xlsx.with_suffix(".md")
    write_tracker_xlsx(result, xlsx)
    write_tracker_md(result, md)
    # Persist the tidy frame so `spinoffs show` can replay the last run offline.
    if not result.df.empty:
        cache.save_df("spinoffs_last", result.df.reset_index())
    result.xlsx_path, result.md_path = xlsx, md


def load_tracker() -> pd.DataFrame | None:
    """The last scan/track frame written by `_write` (for `spinoffs show`)."""
    return cache.load_df("spinoffs_last")


def run_scan(
    forms: list[str] | None = None,
    lookback_days: int | None = None,
    query: str | None = None,
    limit: int | None = None,
    output_path: Path | str | None = None,
    refresh: bool = False,
) -> TrackerResult:
    """Market-wide discovery of recent Form 10 / 10-12B registrations."""
    cfg = load_config()
    edgar = EdgarProvider()
    recs = discovery.scan_recent_form10s(
        edgar,
        forms=forms or cfg.spinoffs.forms,
        lookback_days=lookback_days or cfg.spinoffs.lookback_days,
        query=query or cfg.spinoffs.query,
        limit=limit,
        use_cache=not refresh,
    )
    # Broad/fast: status + metadata only. Valuation/flags stay pending here.
    for r in recs:
        r.q2_valuation_disparity = (
            f"trades as {r.spinco_ticker}" if r.spinco_ticker else "pending (no market price yet)"
        )
    result = TrackerResult(records=recs, df=records_to_df(recs), mode="scan")
    _write(result, output_path, "spinoffs_scan")
    return result


def run_track(
    parent: str,
    spinco: str | None = None,
    output_path: Path | str | None = None,
    refresh: bool = False,
    fetch_softfields: bool = True,
) -> TrackerResult:
    """Deep-dive a parent's spin-off: leverage/quality flags + soft fields."""
    cfg = load_config()
    edgar = EdgarProvider()
    recs = discovery.track_parent(
        edgar, parent, spinco=spinco, forms=cfg.spinoffs.forms, use_cache=not refresh
    )
    for r in recs:
        if not r.error:
            _enrich(edgar, r, fetch_softfields=fetch_softfields, use_cache=not refresh)
    result = TrackerResult(records=recs, df=records_to_df(recs), mode="track")
    _write(result, output_path, f"spinoffs_{parent.upper()}")
    return result

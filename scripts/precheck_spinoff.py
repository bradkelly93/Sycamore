r"""Phase 4 data-shape pre-check (submissions + companyfacts).

Run locally where SEC endpoints are reachable (the web sandbox blocks
*.sec.gov). Standalone — does NOT import sycamore_prep, so it works before any
Phase 4 code exists. Only needs `requests` (already a project dependency).

    .\.venv\Scripts\python.exe scripts\precheck_spinoff.py VLTO
    .\.venv\Scripts\python.exe scripts\precheck_spinoff.py SOLV
    # SpinCos: VLTO SOLV GEV CXT     parents: DHR MMM

Paste the raw output back so the parser/extractor can be calibrated to the real
shapes (Phase-2/3 lesson #1: look, don't guess).
"""

from __future__ import annotations

import collections
import sys

import requests

UA = "sycamore-prep/0.1 bradley.kelly93@gmail.com"  # must match config.yaml edgar.user_agent
S = requests.Session()


def get(url: str):
    host = (
        "efts.sec.gov" if "efts.sec.gov" in url
        else "www.sec.gov" if "www.sec.gov" in url
        else "data.sec.gov"
    )
    r = S.get(url, headers={"User-Agent": UA, "Host": host}, timeout=30)
    r.raise_for_status()
    return r.json()


def resolve(ticker: str) -> tuple[str, str]:
    for row in get("https://www.sec.gov/files/company_tickers.json").values():
        if str(row["ticker"]).upper() in (ticker.upper(), ticker.upper().replace(".", "-")):
            return str(row["cik_str"]).zfill(10), row.get("title", "")
    raise SystemExit(f"{ticker} not found in company_tickers.json")


def main(ticker: str) -> None:
    cik, name = resolve(ticker)
    print(f"== {ticker}  CIK={cik}  {name}")

    # 1) SUBMISSIONS shape -------------------------------------------------
    sub = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rec = sub.get("filings", {}).get("recent", {})
    print("filings.recent keys:", list(rec.keys()))
    print("recent form counts:", collections.Counter(rec.get("form", [])).most_common(12))
    print("older shards (filings.files):",
          [f.get("name") for f in sub.get("filings", {}).get("files", [])][:3])
    forms = rec.get("form", [])
    want, shown = {"10-12B", "10-12B/A", "10-12G", "8-K", "10-K"}, 0
    print("-- sample filings of interest --")
    for i, fm in enumerate(forms):
        if fm in want and shown < 12:
            print(fm,
                  rec["filingDate"][i],
                  "report=" + str(rec.get("reportDate", [""] * len(forms))[i]),
                  rec["accessionNumber"][i],
                  rec.get("primaryDocument", [""] * len(forms))[i],
                  "| items=", rec.get("items", [""] * len(forms))[i])
            shown += 1

    # 2) COMPANYFACTS shape (does XBRL exist for this CIK yet?) ------------
    try:
        gaap = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json") \
            .get("facts", {}).get("us-gaap", {})
        print(f"companyfacts: us-gaap concepts = {len(gaap)}")
        for tag in ("Revenues", "Assets", "StockholdersEquity", "LongTermDebtNoncurrent"):
            ends = [e.get("end") for u in gaap.get(tag, {}).get("units", {}).values() for e in u]
            print(f"  {tag}: {len(ends)} pts, latest_end={max(ends) if ends else None}")
    except Exception as exc:  # noqa: BLE001
        print("companyfacts: NONE / error ->", exc)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "VLTO")

r"""Phase 4 data-shape pre-check (10-12B Information Statement phrasing).

Calibrates the fail-safe soft-field extractor (distribution ratio + record /
distribution dates). Run locally; paste the matched lines back.

    .\.venv\Scripts\python.exe scripts\precheck_infostmt.py VLTO
    .\.venv\Scripts\python.exe scripts\precheck_infostmt.py SOLV

Finds the SpinCo's own 10-12B / 10-12B/A in its submissions, fetches the
primary document (and any EX-99 information statement), strips HTML tags, and
prints the first context window around each ratio/date keyword so the regexes
can be pinned to real phrasing (lesson #1: look, don't guess).
"""

from __future__ import annotations

import re
import sys

import requests

UA = "sycamore-prep/0.1 bradley.kelly93@gmail.com"  # match config.yaml
S = requests.Session()

KEYWORDS = [
    "for every", "for each", "one share of", "shares of common stock",
    "record date", "distribution date", "distribution ratio",
    "will be distributed", "ratio of", "holders of record",
]


def get(url: str, as_json: bool = True):
    host = "www.sec.gov" if "www.sec.gov" in url else "data.sec.gov"
    r = S.get(url, headers={"User-Agent": UA, "Host": host}, timeout=60)
    r.raise_for_status()
    return r.json() if as_json else r.text


def resolve(ticker: str) -> str:
    for row in get("https://www.sec.gov/files/company_tickers.json").values():
        if str(row["ticker"]).upper() in (ticker.upper(), ticker.upper().replace(".", "-")):
            return str(row["cik_str"]).zfill(10)
    raise SystemExit(f"{ticker} not found in company_tickers.json")


def scan_text(label: str, html: str) -> None:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    print(f"\n===== {label}  (clean len={len(text)}) =====")
    for kw in KEYWORDS:
        m = re.search(re.escape(kw), text, re.I)
        if m:
            s = max(0, m.start() - 140)
            print(f"[{kw}] …{text[s:m.start() + 200]}…")


def main(ticker: str) -> None:
    cik = resolve(ticker)
    cik_int = int(cik)
    filings = get(f"https://data.sec.gov/submissions/CIK{cik}.json")["filings"]
    rec = filings["recent"]
    idx = [i for i, f in enumerate(rec["form"]) if f in ("10-12B", "10-12B/A", "10-12G")]
    if not idx:
        print(f"No 10-12B in recent for {ticker}. Older shards: "
              f"{[f['name'] for f in filings.get('files', [])]}")
        return
    print(f"{ticker} (CIK {cik}) 10-12B filings in recent (most-recent first):")
    for i in idx:
        print("  ", rec["form"][i], rec["filingDate"][i], rec["accessionNumber"][i],
              rec["primaryDocument"][i])

    i = idx[0]  # latest amendment (recent is most-recent-first)
    accn_nd = rec["accessionNumber"][i].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nd}"
    names = [it["name"] for it in get(f"{base}/index.json")["directory"]["item"]
             if str(it["name"]).lower().endswith((".htm", ".html"))]
    print("\ndocuments in that filing:", names[:25])

    order = [rec["primaryDocument"][i]] + [n for n in names if "99" in n]
    seen: set[str] = set()
    for doc in order:
        if not doc or doc in seen:
            continue
        seen.add(doc)
        try:
            scan_text(doc, get(f"{base}/{doc}", as_json=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  (could not fetch {doc}: {exc})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "VLTO")

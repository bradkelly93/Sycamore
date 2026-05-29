"""Phase 4 data-shape pre-check (EDGAR full-text search / efts.sec.gov).

Run locally where SEC endpoints are reachable (the web sandbox blocks
efts.sec.gov). Confirms the full-text-search hit schema and whether the
`forms=10-12B` filter behaves as expected for market-wide spin-off discovery.

    .\.venv\Scripts\python.exe scripts\precheck_fts.py

Paste the raw output back (especially the full first `_source`).
"""

from __future__ import annotations

import json

import requests

UA = "sycamore-prep/0.1 bradley.kelly93@gmail.com"  # must match config.yaml

url = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22information+statement%22&forms=10-12B&startdt=2023-01-01&enddt=2024-12-31"
)
j = requests.get(url, headers={"User-Agent": UA, "Host": "efts.sec.gov"}, timeout=30).json()
hits = j.get("hits", {}).get("hits", [])
print("hits.total:", j.get("hits", {}).get("total"))
print(f"returned {len(hits)} hits; first 5:")
for h in hits[:5]:
    s = h.get("_source", {})
    print(" ", h.get("_id"), "|", s.get("file_date"), "|", s.get("display_names"))
print("\nFULL first hit _source:")
print(json.dumps(hits[0].get("_source", {}), indent=2) if hits else "no hits")

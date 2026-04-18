"""8-K material event filings via EDGAR full-text search.

8-K reports disclose specific "material events" — SEC defines dozens of
numbered items (1.01 material agreement, 1.03 bankruptcy, 2.01 acquisition,
2.02 earnings release, 4.01 auditor change, 4.02 non-reliance on financials,
5.02 officer/director change, 7.01 Reg FD, 8.01 other events, 9.01 exhibits).

EFTS exposes the `items` array on each 8-K hit, so we get item-level taxonomy
without fetching the filing body. Exactly what we need.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Common item codes → friendly labels for UI
ITEM_LABELS: dict[str, str] = {
    "1.01": "Material definitive agreement",
    "1.02": "Termination of material agreement",
    "1.03": "Bankruptcy / receivership",
    "2.01": "Completion of acquisition or disposition",
    "2.02": "Results of operations (earnings)",
    "2.03": "Creation of material direct financial obligation",
    "2.04": "Triggering event accelerating obligation",
    "2.05": "Costs associated with exit / disposal",
    "2.06": "Material impairments",
    "3.01": "Delisting / failure to satisfy listing rule",
    "3.02": "Unregistered sale of equity securities",
    "3.03": "Material modification to rights of security holders",
    "4.01": "Change in auditor",
    "4.02": "Non-reliance on prior financial statements (restatement)",
    "5.01": "Change in control",
    "5.02": "Departure/appointment of officer or director",
    "5.03": "Amendment to articles or bylaws",
    "5.07": "Submission of matters to shareholder vote",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}

# Default "interesting" items for the events dashboard — the ones with documented
# price reactions. Users can toggle others on.
DEFAULT_INTERESTING = {
    "1.01",  # M&A / material agreements
    "1.03",  # bankruptcy
    "2.01",  # acquisition complete
    "2.02",  # earnings
    "2.06",  # impairments
    "3.01",  # delisting
    "4.01",  # auditor change
    "4.02",  # restatement
    "5.02",  # exec departure
    "8.01",  # other (often buybacks / material events without a specific code)
}

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class Filing8K:
    accession: str
    filing_date: str
    issuer_cik: int | None
    issuer_name: str
    issuer_ticker: str | None
    items: list[str]
    primary_doc: str


def _parse_display_name(s: str) -> tuple[str, int | None, str | None]:
    tic_match = _TICKER_RE.search(s)
    cik_match = _CIK_RE.search(s)
    name = _CIK_RE.sub("", s)
    name = _TICKER_RE.sub("", name)
    name = re.sub(r"\s{2,}", " ", name).strip().rstrip("(").strip()
    cik = int(cik_match.group(1)) if cik_match else None
    ticker = tic_match.group(1) if tic_match else None
    return name, cik, ticker


def fetch_window(start: str, end: str) -> list[Filing8K]:
    """Scrape 8-K filings for [start, end] via EFTS pagination."""
    out: list[Filing8K] = []
    offset = 0
    while True:
        params = {
            "q": "",
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start,
            "enddt": end,
            "from": offset,
        }
        try:
            r = httpx.get(EFTS_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! 8-K EFTS page {offset} failed: {e}")
            break
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        if not hits:
            break
        for h in hits:
            _id = h.get("_id", "")
            if ":" not in _id:
                continue
            accession, primary = _id.split(":", 1)
            src = h.get("_source", {})
            names = src.get("display_names") or []
            issuer_name = issuer_cik = issuer_ticker = None
            if names:
                issuer_name, issuer_cik, issuer_ticker = _parse_display_name(names[0])
            raw_items = src.get("items") or []
            items = [str(i) for i in raw_items if i]
            out.append(
                Filing8K(
                    accession=accession,
                    filing_date=src.get("file_date") or "",
                    issuer_cik=issuer_cik,
                    issuer_name=issuer_name or "",
                    issuer_ticker=issuer_ticker,
                    items=items,
                    primary_doc=primary,
                )
            )
        offset += len(hits)
        if offset >= total:
            break
    return out

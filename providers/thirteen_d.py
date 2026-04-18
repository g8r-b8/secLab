"""Schedule 13D / 13D-A activism tracker.

SEC doesn't bulk-publish 13D data. We scrape EDGAR full-text search (EFTS) for
metadata only — no individual-doc fetching needed for the MVP. EFTS gives us
issuer name + ticker + CIK, filer name + CIK, filing date, and accession.

Historical abnormal returns post-filing are well-documented (academic research:
Brav/Jiang/Partnoy/Thomas 2008 onward). The intent field in Item 4 distinguishes
active vs passive — that lives in the filing body, not EFTS metadata, so v1 just
indexes metadata and we leave body-parsing for a later pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}

# EFTS calls are lightweight metadata queries — higher concurrency is fine since
# we don't fetch individual docs. Still stay polite.
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Regex to extract ticker from EFTS display_name format:
#   "APPLE INC  (AAPL)  (CIK 0000320193)"
_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class Filing13D:
    accession: str
    form: str              # "SCHEDULE 13D" or "SCHEDULE 13D/A"
    filing_date: str       # ISO YYYY-MM-DD
    issuer_cik: int | None
    issuer_name: str
    issuer_ticker: str | None
    filer_cik: int | None
    filer_name: str
    primary_doc: str       # relative URL component


def _parse_display_name(s: str) -> tuple[str, int | None, str | None]:
    """Return (name, cik, ticker_or_None).

    EFTS display_names items look like:
      "APPLE INC  (AAPL)  (CIK 0000320193)"
      "JANE SMITH  (CIK 0001234567)"
    """
    tic_match = _TICKER_RE.search(s)
    cik_match = _CIK_RE.search(s)
    # Strip trailing "(TICKER)" and "(CIK ...)" fragments for clean name
    name = _CIK_RE.sub("", s)
    name = _TICKER_RE.sub("", name)
    name = re.sub(r"\s{2,}", " ", name).strip().rstrip("(").strip()
    cik = int(cik_match.group(1)) if cik_match else None
    ticker = tic_match.group(1) if tic_match else None
    return name, cik, ticker


def _fetch_page(form: str, start: str, end: str, offset: int) -> dict:
    params = {
        "q": "",
        "forms": form,
        "dateRange": "custom",
        "startdt": start,
        "enddt": end,
        "from": offset,
    }
    r = httpx.get(EFTS_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_window(start: str, end: str) -> list[Filing13D]:
    """Scrape SCHEDULE 13D + SCHEDULE 13D/A filings for [start, end]."""
    out: list[Filing13D] = []
    for form in ("SCHEDULE 13D", "SCHEDULE 13D/A"):
        offset = 0
        while True:
            try:
                data = _fetch_page(form, start, end, offset)
            except Exception as e:
                print(f"  ! 13D EFTS page {offset} ({form}) failed: {e}")
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
                # First name is the issuer (has ticker), second is the filer
                issuer_name = issuer_cik = issuer_ticker = None
                filer_name = filer_cik = None
                if names:
                    issuer_name, issuer_cik, issuer_ticker = _parse_display_name(names[0])
                if len(names) >= 2:
                    filer_name, filer_cik, _ = _parse_display_name(names[1])
                out.append(
                    Filing13D(
                        accession=accession,
                        form=src.get("form") or form,
                        filing_date=src.get("file_date") or "",
                        issuer_cik=issuer_cik,
                        issuer_name=issuer_name or "",
                        issuer_ticker=issuer_ticker,
                        filer_cik=filer_cik,
                        filer_name=filer_name or "",
                        primary_doc=primary,
                    )
                )
            offset += len(hits)
            if offset >= total:
                break
    return out


def as_dict(f: Filing13D) -> dict:
    return {
        "accession": f.accession,
        "form": f.form,
        "filing_date": f.filing_date,
        "issuer_cik": f.issuer_cik,
        "issuer_name": f.issuer_name,
        "issuer_ticker": f.issuer_ticker,
        "filer_cik": f.filer_cik,
        "filer_name": f.filer_name,
        "primary_doc": f.primary_doc,
    }

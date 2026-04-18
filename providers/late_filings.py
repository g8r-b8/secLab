"""NT 10-K / NT 10-Q late-filing tracker.

Filed when a company can't file its 10-K or 10-Q on time (a notification under
Rule 12b-25). Empirically these precede negative news a majority of the time —
restatements, auditor issues, liquidity crises. Clean short/avoid signal.

EFTS scrapes both the NT 10-K / NT 10-Q / NT 10-K/A / NT 10-Q/A variants; we
store the 'root form' so the UI can filter/group cleanly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
FORMS = ["NT 10-K", "NT 10-Q"]  # EFTS includes /A amendments automatically

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class LateFiling:
    accession: str
    form: str                 # "NT 10-K", "NT 10-Q", "NT 10-K/A", "NT 10-Q/A"
    filing_date: str
    issuer_cik: int | None
    issuer_name: str
    issuer_ticker: str | None
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


class EftsError(Exception):
    """Raised when EFTS can't be reached — caller must not mark days ingested."""


def fetch_window(start: str, end: str) -> list[LateFiling]:
    """Raises EftsError if EFTS fails so the caller can avoid poisoning the cache."""
    import time as _time

    out: list[LateFiling] = []
    any_success = False
    for form in FORMS:
        offset = 0
        while True:
            params = {
                "q": "",
                "forms": form,
                "dateRange": "custom",
                "startdt": start,
                "enddt": end,
                "from": offset,
            }
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    r = httpx.get(EFTS_URL, params=params, headers=HEADERS, timeout=30)
                    r.raise_for_status()
                    data = r.json()
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    _time.sleep(1 + attempt * 2)
            if last_err:
                print(f"  ! NT filings EFTS ({form}) failed after retries: {last_err}")
                break
            any_success = True
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
                out.append(
                    LateFiling(
                        accession=accession,
                        form=src.get("form") or form,
                        filing_date=src.get("file_date") or "",
                        issuer_cik=issuer_cik,
                        issuer_name=issuer_name or "",
                        issuer_ticker=issuer_ticker,
                        primary_doc=primary,
                    )
                )
            offset += len(hits)
            if offset >= total:
                break
    if not any_success:
        raise EftsError(f"All EFTS calls for {start} → {end} failed")
    seen: set[str] = set()
    unique: list[LateFiling] = []
    for f in out:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        unique.append(f)
    return unique

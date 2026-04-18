"""S-3 / 424B secondary-offering tracker.

S-3 (shelf registration) is how public companies pre-register securities for
future issuance. When they actually issue, a 424B prospectus supplement hits
with the pricing. Both are dilution events — historically announce-day price
drops of 3-10% for secondaries, smaller for "at-the-market" programs.

We track both the shelf (forward-looking inventory) and the takedowns (actual
dilution happening now).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Forms of interest — S-3/S-3ASR register new shelves; 424B5/424B7 are the
# actual takedown prospectuses that price specific offerings.
FORMS = ["S-3", "S-3ASR", "S-3/A", "424B5", "424B7", "424B2"]

FORM_LABEL: dict[str, str] = {
    "S-3":    "Shelf registration",
    "S-3ASR": "Automatic shelf registration (WKSI)",
    "S-3/A":  "Shelf amendment",
    "424B5":  "Takedown prospectus",
    "424B7":  "Selling-stockholder takedown",
    "424B2":  "Takedown pricing supplement",
}

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class SecondaryFiling:
    accession: str
    form: str
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


def fetch_window(start: str, end: str) -> list[SecondaryFiling]:
    out: list[SecondaryFiling] = []
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
            try:
                r = httpx.get(EFTS_URL, params=params, headers=HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"  ! secondaries EFTS ({form}) failed: {e}")
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
                out.append(
                    SecondaryFiling(
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
    seen: set[str] = set()
    unique: list[SecondaryFiling] = []
    for f in out:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        unique.append(f)
    return unique

"""Tender offer tracker — SC TO-T, SC TO-C, SC 14D9.

An acquirer makes a direct offer to target-company shareholders to buy shares
at a premium. These are unambiguous M&A events with pre-announced prices —
the target's stock typically snaps toward the offer price, sometimes with
arbitrage spread if multiple outcomes remain possible.

  SC TO-T  — tender offer by third party (most common acquirer-initiated)
  SC TO-C  — communications related to tender offer
  SC TO-I  — issuer tender offer (buyback via tender)
  SC 14D9  — target's response / recommendation to shareholders
  SC 14D9/A — target response amendments
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

FORMS = ["SC TO-T", "SC TO-I", "SC TO-C", "SC 14D9", "SC 14D9/A"]

FORM_LABEL: dict[str, str] = {
    "SC TO-T":  "Third-party tender offer (acquirer)",
    "SC TO-I":  "Issuer tender offer (self-tender buyback)",
    "SC TO-C":  "Tender-offer communications",
    "SC 14D9":  "Target recommendation",
    "SC 14D9/A": "Target recommendation amendment",
}

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class TenderFiling:
    accession: str
    form: str
    filing_date: str
    subject_cik: int | None       # target company (for TO-T/14D9) or issuer (for TO-I)
    subject_name: str
    subject_ticker: str | None
    filer_cik: int | None         # acquirer (or the issuer itself for TO-I)
    filer_name: str
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


def fetch_window(start: str, end: str) -> list[TenderFiling]:
    out: list[TenderFiling] = []
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
                print(f"  ! tender-offer EFTS ({form}) failed: {e}")
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
                subj_name = subj_cik = subj_ticker = None
                if names:
                    subj_name, subj_cik, subj_ticker = _parse_display_name(names[0])
                filer_name = filer_cik = None
                if len(names) >= 2:
                    filer_name, filer_cik, _ = _parse_display_name(names[1])
                out.append(
                    TenderFiling(
                        accession=accession,
                        form=src.get("form") or form,
                        filing_date=src.get("file_date") or "",
                        subject_cik=subj_cik,
                        subject_name=subj_name or "",
                        subject_ticker=subj_ticker,
                        filer_cik=filer_cik,
                        filer_name=filer_name or "",
                        primary_doc=primary,
                    )
                )
            offset += len(hits)
            if offset >= total:
                break
    seen: set[str] = set()
    unique: list[TenderFiling] = []
    for f in out:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        unique.append(f)
    return unique

"""Activist proxy-contest tracker — DFAN14A / DEFC14A / PRRN14A / DFRN14A.

Regular DEF 14A filings are noise (every public co files one annually). The
interesting set is the non-company and contestant variants:

  DFAN14A  — definitive additional material by non-company filer (often activist)
  DEFC14A  — definitive proxy statement by contestant (explicit slate)
  PRRN14A  — preliminary revised non-company proxy (earlier campaign stage)
  DFRN14A  — definitive revised non-company proxy

Each flags a real or brewing governance fight — binary event signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

FORMS = ["DFAN14A", "DEFC14A", "PRRN14A", "DFRN14A", "PREN14A"]

FORM_TONE: dict[str, str] = {
    "DFAN14A": "additional",
    "DEFC14A": "contest",
    "PRRN14A": "preliminary",
    "DFRN14A": "revised",
    "PREN14A": "preliminary",
}

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class ProxyFiling:
    accession: str
    form: str
    filing_date: str
    issuer_cik: int | None
    issuer_name: str
    issuer_ticker: str | None
    filer_cik: int | None
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


def fetch_window(start: str, end: str) -> list[ProxyFiling]:
    out: list[ProxyFiling] = []
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
                print(f"  ! proxy-contest EFTS ({form}) failed: {e}")
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
                filer_name = filer_cik = None
                if len(names) >= 2:
                    filer_name, filer_cik, _ = _parse_display_name(names[1])
                out.append(
                    ProxyFiling(
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
    # Dedupe on accession
    seen: set[str] = set()
    unique: list[ProxyFiling] = []
    for f in out:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        unique.append(f)
    return unique

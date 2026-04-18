"""Shared EFTS scraper for single-filer forms where metadata is enough.

Rather than copy-paste three more nearly-identical providers for SC 13G,
Form 3, and Form D, this one has a generic fetcher parameterised by form
code. Each caller gets typed results via a thin wrapper.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s+\(CIK")
_CIK_RE = re.compile(r"\(CIK\s+0*(\d+)\)")


@dataclass
class GenericFiling:
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


def fetch_efts(
    forms: Iterable[str],
    start: str,
    end: str,
    *,
    log_prefix: str = "filing",
) -> list[GenericFiling]:
    """Paginate EFTS for every form in `forms` and merge into one deduped list."""
    import logging
    log = logging.getLogger("seclab")
    out: list[GenericFiling] = []
    for form in forms:
        offset = 0
        log.info(f"  {log_prefix}: starting EFTS scrape {form} {start} → {end}")
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
                print(f"  ! {log_prefix} EFTS ({form}) failed: {e}")
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
                    GenericFiling(
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
            if offset % 500 == 0 and total > offset:
                log.info(f"  {log_prefix}: {form} {offset}/{total}")
            if offset >= total:
                break
        log.info(f"  {log_prefix}: {form} done, {offset} filings")
    seen: set[str] = set()
    unique: list[GenericFiling] = []
    for f in out:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        unique.append(f)
    return unique


# ─── Typed wrappers ────────────────────────────────────────────────────────

def fetch_13g(start: str, end: str) -> list[GenericFiling]:
    return fetch_efts(["SCHEDULE 13G", "SCHEDULE 13G/A"], start, end, log_prefix="13G")


def fetch_form3(start: str, end: str) -> list[GenericFiling]:
    # Form 3 alone = "3"; amendments = "3/A"
    return fetch_efts(["3", "3/A"], start, end, log_prefix="Form3")


def fetch_formD(start: str, end: str) -> list[GenericFiling]:
    return fetch_efts(["D", "D/A"], start, end, log_prefix="FormD")


def fetch_delistings(start: str, end: str) -> list[GenericFiling]:
    """Form 15 (termination of registration) + Form 25 (delisting notice)."""
    return fetch_efts(
        ["15-12B", "15-12G", "15-15D", "15-12B/A", "15-12G/A",
         "25", "25/A", "25-NSE", "25-NSE/A"],
        start, end, log_prefix="delisting",
    )


def fetch_foreign_issuers(start: str, end: str) -> list[GenericFiling]:
    """20-F (foreign annual), 6-K (foreign current report), NT 20-F (late)."""
    return fetch_efts(
        ["20-F", "20-F/A", "6-K", "6-K/A", "NT 20-F"],
        start, end, log_prefix="foreign",
    )

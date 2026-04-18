"""13F-HR (institutional holdings) scraper for curated superinvestor funds.

Institutional managers with >$100M AUM must file 13F-HR quarterly, 45 days after
quarter-end. The informationTable.xml has per-position rows: issuer, class,
cusip, USD value, share count, put/call flag.

Academic research documents quarter-over-quarter portfolio changes as a
coat-tailing signal — new positions and large loads from storied managers
(Buffett, Ackman, Burry, Druckenmiller, Tepper, Icahn) tend to outperform.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"


# Curated starter set — user can add more via add_fund() API call.
# Each entry: (cik, short_label, long_name).
# CIK is the management entity that files the 13F.
CURATED_FUNDS: list[tuple[int, str, str]] = [
    (1067983, "Berkshire",    "Berkshire Hathaway Inc. (Buffett)"),
    (1649339, "Scion",        "Scion Asset Management (Michael Burry)"),
    (1336528, "Pershing",     "Pershing Square Capital (Bill Ackman)"),
    (1040273, "Third Point",  "Third Point LLC (Dan Loeb)"),
    (1656456, "Appaloosa",    "Appaloosa LP (David Tepper)"),
    (1536411, "Duquesne",     "Duquesne Family Office (Stanley Druckenmiller)"),
    (1079114, "Greenlight",   "Greenlight Capital (David Einhorn)"),
    (921669,  "Icahn",        "Icahn Capital LP (Carl Icahn)"),
    (1167483, "Tiger Global", "Tiger Global Management"),
    (1103804, "Viking",       "Viking Global Investors"),
    (1135730, "Coatue",       "Coatue Management"),
    (1418814, "ValueAct",     "ValueAct Holdings"),
]


@dataclass
class Filing13F:
    filer_cik: int
    accession: str
    period_end: str          # ISO YYYY-MM-DD
    filed_date: str          # ISO YYYY-MM-DD
    form: str                # '13F-HR' or '13F-HR/A'


@dataclass
class Position:
    issuer_name: str
    title_of_class: str
    cusip: str
    value_usd: float         # value in USD (SEC files in USD, not thousands)
    shares: float
    put_call: str            # 'PUT', 'CALL', or '' (stock)


def list_13f_filings(cik: int, max_filings: int = 8) -> list[Filing13F]:
    """Return most-recent 13F-HR filings for a CIK, newest first."""
    cik10 = f"{cik:010d}"
    r = httpx.get(SUBMISSIONS_URL.format(cik10=cik10), headers=HEADERS, timeout=30)
    r.raise_for_status()
    d = r.json()
    recent = d.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    out: list[Filing13F] = []
    for form, acc, fdate, rdate in zip(forms, accessions, dates, report_dates):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        out.append(
            Filing13F(
                filer_cik=cik,
                accession=acc,
                period_end=rdate or "",
                filed_date=fdate or "",
                form=form,
            )
        )
        if len(out) >= max_filings:
            break
    return out


_TAG_RE_CACHE: dict[str, re.Pattern] = {}


def _first(text: str, tag: str) -> str:
    pat = _TAG_RE_CACHE.get(tag)
    if pat is None:
        # Allow optional namespace prefix, e.g. <ns1:nameOfIssuer>...
        pat = re.compile(rf"<(?:[a-zA-Z0-9_]+:)?{tag}[^>]*>([^<]*)</(?:[a-zA-Z0-9_]+:)?{tag}>")
        _TAG_RE_CACHE[tag] = pat
    m = pat.search(text)
    return m.group(1).strip() if m else ""


def _float(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def fetch_positions(cik: int, accession: str) -> list[Position]:
    """Fetch the informationTable XML for a given filing and parse rows."""
    acc_nodash = accession.replace("-", "")
    base = f"{ARCHIVE_BASE}/{cik}/{acc_nodash}"
    # Find the info table XML via index.json
    idx = httpx.get(f"{base}/index.json", headers=HEADERS, timeout=30)
    idx.raise_for_status()
    items = idx.json().get("directory", {}).get("item", [])
    xml_files = [it["name"] for it in items if it["name"].lower().endswith(".xml")]
    info_name: str | None = None
    for n in xml_files:
        low = n.lower()
        if "information" in low or "infotable" in low:
            info_name = n
            break
    if info_name is None:
        # Fall back: any xml that isn't primary_doc or form13fInfoTable cover
        for n in xml_files:
            if "primary" not in n.lower() and "coverpage" not in n.lower():
                info_name = n
                break
    if info_name is None:
        return []
    r = httpx.get(f"{base}/{info_name}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    text = r.text

    # Split into infoTable blocks (strip namespace prefixes for simplicity)
    blocks = re.findall(
        r"<(?:[a-zA-Z0-9_]+:)?infoTable[^>]*>.*?</(?:[a-zA-Z0-9_]+:)?infoTable>",
        text,
        re.DOTALL,
    )
    out: list[Position] = []
    for blk in blocks:
        name = _first(blk, "nameOfIssuer")
        title = _first(blk, "titleOfClass")
        cusip = _first(blk, "cusip")
        value = _float(_first(blk, "value"))
        # Shares live inside <shrsOrPrnAmt><sshPrnamt>
        shares = _float(_first(blk, "sshPrnamt"))
        put_call = _first(blk, "putCall")
        out.append(
            Position(
                issuer_name=name,
                title_of_class=title,
                cusip=cusip,
                value_usd=value,
                shares=shares,
                put_call=put_call,
            )
        )
    return out

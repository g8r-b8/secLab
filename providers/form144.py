"""Form 144 — notice of proposed sale by insiders.

Filed ~30 days before a planned sale. Forward-looking sell signal.
SEC does not bulk-publish these, so we scrape EDGAR with a throttled fetcher.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
# SEC cap is 10 req/s per IP. Four in-flight with keepalive stays safely under
# the threshold while giving decent throughput.
CONNECTIONS = 4
MAX_INFLIGHT = 4
EFTS_URL = (
    "https://efts.sec.gov/LATEST/search-index?q=&forms=144"
    "&dateRange=custom&startdt={start}&enddt={end}"
    "&from={offset}&size={size}"
)


@dataclass
class Form144Record:
    accession: str
    filing_date: str | None
    issuer_cik: int | None
    issuer_name: str
    seller_name: str
    relationship: str
    class_title: str
    shares: float | None
    aggregate_value: float | None
    approx_sale_date: str | None
    exchange: str
    broker_name: str


# ─── EFTS index ─────────────────────────────────────────────────────────────

def fetch_accessions(start: str, end: str) -> list[tuple[str, int, str, str]]:
    """Return list of (accession, cik, primary_doc, filing_date)."""
    out: list[tuple[str, int, str, str]] = []
    offset = 0
    size = 100
    while True:
        url = EFTS_URL.format(start=start, end=end, offset=offset, size=size)
        try:
            r = httpx.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! EFTS page {offset} failed: {e}")
            break
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            _id = h.get("_id", "")
            src = h.get("_source", {})
            if ":" not in _id:
                continue
            accession, primary = _id.split(":", 1)
            ciks = src.get("ciks") or []
            if not ciks:
                continue
            cik = int(str(ciks[0]).lstrip("0") or 0)
            if not cik:
                continue
            filed = src.get("file_date") or ""
            out.append((accession, cik, primary, filed))
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(hits)
        if offset >= total:
            break
    return out


# ─── XML parse ──────────────────────────────────────────────────────────────

_TAG_RE_CACHE: dict[str, re.Pattern] = {}


def _first(text: str, tag: str) -> str:
    pat = _TAG_RE_CACHE.get(tag)
    if pat is None:
        pat = re.compile(rf"<{tag}[^>]*>([^<]*)</{tag}>")
        _TAG_RE_CACHE[tag] = pat
    m = pat.search(text)
    return m.group(1).strip() if m else ""


def _all(text: str, tag: str) -> list[str]:
    return [m.strip() for m in re.findall(rf"<{tag}[^>]*>([^<]*)</{tag}>", text)]


def _parse_approx_date(s: str) -> str | None:
    """'04/14/2026' → '2026-04-14'. Other formats → None."""
    if not s:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s.strip())
    if not m:
        return None
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


def _parse_float(s: str) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_form144(text: str, accession: str, filing_date: str) -> Form144Record | None:
    issuer_cik_str = _first(text, "issuerCik").lstrip("0")
    try:
        issuer_cik = int(issuer_cik_str) if issuer_cik_str else None
    except ValueError:
        issuer_cik = None
    issuer_name = _first(text, "issuerName")
    seller = _first(text, "nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold")
    rels = _all(text, "relationshipToIssuer")
    if not issuer_name and not seller:
        return None
    # Pull first securitiesInformation block only — some filings have multiple
    # sub-sales; we aggregate shares/value across them.
    total_shares = 0.0
    total_value = 0.0
    blocks = re.findall(
        r"<securitiesInformation>(.*?)</securitiesInformation>", text, re.DOTALL
    )
    class_title = ""
    approx_date: str | None = None
    exchange = ""
    broker = ""
    for blk in blocks:
        sh = _parse_float(_first(blk, "noOfUnitsSold"))
        vl = _parse_float(_first(blk, "aggregateMarketValue"))
        if sh:
            total_shares += sh
        if vl:
            total_value += vl
        # Take the first block's metadata as canonical
        if not class_title:
            class_title = _first(blk, "securitiesClassTitle")
        if not approx_date:
            approx_date = _parse_approx_date(_first(blk, "approxSaleDate"))
        if not exchange:
            exchange = _first(blk, "securitiesExchangeName")
        if not broker:
            broker = _first(blk, "name")
    return Form144Record(
        accession=accession,
        filing_date=filing_date or None,
        issuer_cik=issuer_cik,
        issuer_name=issuer_name,
        seller_name=seller,
        relationship=" / ".join(rels),
        class_title=class_title,
        shares=total_shares or None,
        aggregate_value=total_value or None,
        approx_sale_date=approx_date,
        exchange=exchange,
        broker_name=broker,
    )


# ─── Throttled async fetch ──────────────────────────────────────────────────

async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    cik: int,
    accession: str,
    primary: str,
    filed: str,
    stats: dict,
) -> Form144Record | None:
    acc_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary}"
    async with sem:
        for attempt in range(3):
            try:
                r = await client.get(url, timeout=20)
            except Exception as e:
                stats["exc"] += 1
                if attempt == 2:
                    print(f"  ! {accession}: {type(e).__name__}: {e}")
                    return None
                await asyncio.sleep(1 + attempt)
                continue
            if r.status_code == 429:
                stats["429"] += 1
                retry = int(r.headers.get("retry-after", "2"))
                await asyncio.sleep(min(retry, 10))
                continue
            if r.status_code >= 400:
                stats["other_http"] += 1
                return None
            stats["ok"] += 1
            return parse_form144(r.text, accession, filed)
    return None


async def fetch_window_async(
    accessions: list[tuple[str, int, str, str]],
) -> list[Form144Record]:
    stats = {"ok": 0, "429": 0, "other_http": 0, "exc": 0}
    limits = httpx.Limits(max_connections=CONNECTIONS, max_keepalive_connections=CONNECTIONS)
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    records: list[Form144Record] = []
    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        tasks = [
            _fetch_one(client, sem, cik, acc, primary, filed, stats)
            for acc, cik, primary, filed in accessions
        ]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            rec = await coro
            if rec:
                records.append(rec)
            if i % 100 == 0:
                print(
                    f"  … {i}/{len(accessions)} parsed={len(records)} "
                    f"ok={stats['ok']} 429={stats['429']} 4xx/5xx={stats['other_http']}"
                )
    print(f"  done. ok={stats['ok']} 429={stats['429']} 4xx/5xx={stats['other_http']} exc={stats['exc']}")
    return records


def fetch_window(start: str, end: str) -> list[Form144Record]:
    accs = fetch_accessions(start, end)
    print(f"  EFTS returned {len(accs)} Form 144 filings for {start} → {end}")
    if not accs:
        return []
    return asyncio.run(fetch_window_async(accs))


def record_as_dict(r: Form144Record) -> dict[str, Any]:
    return {
        "accession": r.accession,
        "filing_date": r.filing_date,
        "issuer_cik": r.issuer_cik,
        "issuer_name": r.issuer_name,
        "seller_name": r.seller_name,
        "relationship": r.relationship,
        "class_title": r.class_title,
        "shares": r.shares,
        "aggregate_value": r.aggregate_value,
        "approx_sale_date": r.approx_sale_date,
        "exchange": r.exchange,
        "broker_name": r.broker_name,
    }

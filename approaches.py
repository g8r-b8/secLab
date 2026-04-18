"""Profile 4 approaches to getting Form 4 purchase data. Run each and time it."""
import asyncio
import time
import re
from io import StringIO

import httpx
import pandas as pd
from lxml import etree

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
TARGET_DATE = "2026-04-14"


# ─── Approach A: edgartools (baseline) ───────────────────────────────────────

def approach_a_edgartools(n=50):
    """edgartools .obj() per filing — baseline."""
    from edgar import set_identity, get_filings
    set_identity("wingatejones@gmail.com")
    filings = get_filings(form="4", year=2026, quarter=2).filter(date=TARGET_DATE)
    total = len(filings)
    t0 = time.perf_counter()
    rows = []
    for i in range(min(n, total)):
        try:
            df = filings[i].obj().to_dataframe()
            if df is not None and "Code" in df.columns:
                rows.extend(df[df["Code"] == "P"].to_dict("records"))
        except Exception:
            pass
    elapsed = time.perf_counter() - t0
    return elapsed, len(rows), total


# ─── Approach B: EDGAR full-index → async XML fetch ─────────────────────────

def get_form4_accessions(date_str: str) -> list[tuple[str, str]]:
    """Pull the quarterly full-index and return (cik, accession) for Form 4 on date."""
    y, m, _ = date_str.split("-")
    q = (int(m) - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx"
    r = httpx.get(url, headers=HEADERS, timeout=30)
    lines = r.text.splitlines()
    results = []
    for line in lines:
        if not line.startswith("4 "):
            continue
        # format: Form-Type  Company  CIK  Date-filed  Filename
        parts = line.split()
        if parts[0] != "4":
            continue
        filed = parts[-2]
        if filed != date_str:
            continue
        filename = parts[-1]  # edgar/data/CIK/accession-index.htm
        cik = filename.split("/")[2]
        raw = filename.split("/")[3]
        accession = raw.replace("-index.htm", "").replace(".txt", "")
        results.append((cik, accession))
    return results


def accession_to_xml_url(cik: str, accession: str) -> str:
    acc_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession}.txt"


def parse_form4_xml(text: str) -> list[dict]:
    """Extract P-code transactions from raw Form 4 XML/SGML."""
    # Form 4 XMLs are wrapped in SGML; strip to <ownershipDocument>
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.DOTALL)
    if not m:
        return []
    try:
        root = etree.fromstring(m.group().encode())
    except Exception:
        return []
    rows = []
    issuer = root.findtext(".//issuerName") or ""
    ticker = root.findtext(".//issuerTradingSymbol") or ""
    insider = root.findtext(".//rptOwnerName") or ""
    position = root.findtext(".//officerTitle") or root.findtext(".//relationship") or ""
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = tx.findtext(".//transactionCode") or ""
        if code != "P":
            continue
        try:
            shares = float(tx.findtext(".//transactionShares/value") or 0)
            price = float(tx.findtext(".//transactionPricePerShare/value") or 0)
        except ValueError:
            shares = price = 0.0
        rows.append({"ticker": ticker, "issuer": issuer, "insider": insider,
                     "position": position, "shares": shares, "price": price,
                     "value": shares * price, "code": code})
    return rows


async def fetch_one(client: httpx.AsyncClient, cik: str, accession: str) -> list[dict]:
    url = accession_to_xml_url(cik, accession)
    try:
        r = await client.get(url, timeout=15)
        return parse_form4_xml(r.text)
    except Exception:
        return []


async def approach_b_async(n=50):
    """Direct async httpx + lxml XML parsing."""
    accessions = get_form4_accessions(TARGET_DATE)[:n]
    t0 = time.perf_counter()
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        tasks = [fetch_one(client, cik, acc) for cik, acc in accessions]
        results = await asyncio.gather(*tasks)
    rows = [r for batch in results for r in batch]
    elapsed = time.perf_counter() - t0
    return elapsed, len(rows), len(accessions)


# ─── Approach C: EDGAR EFTS full-text search ────────────────────────────────

def approach_c_efts(n=50):
    """EDGAR full-text search API — returns filing metadata instantly."""
    url = "https://efts.sec.gov/LATEST/search-index?q=%22transactionCode%22&dateRange=custom&startdt={d}&enddt={d}&forms=4&hits.hits.total.value=true&hits.hits._source.period_of_report=true".format(d=TARGET_DATE)
    url = f"https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt={TARGET_DATE}&enddt={TARGET_DATE}&hits.hits._source=file_date,entity_name,file_num,period_of_report"
    t0 = time.perf_counter()
    r = httpx.get(url, headers=HEADERS, timeout=30)
    elapsed = time.perf_counter() - t0
    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    return elapsed, len(hits), data.get("hits", {}).get("total", {}).get("value", 0)


# ─── Approach D: EDGAR company_search + submissions JSON ────────────────────

def approach_d_submissions(tickers=("AAPL", "MSFT", "TSLA", "JPM", "AMZN",
                                     "NVDA", "META", "GOOGL", "BRK-B", "XOM")):
    """Fetch submissions.json per company to extract Form 4 filings list."""
    # First resolve tickers to CIKs
    t0 = time.perf_counter()
    r = httpx.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=20)
    tickers_map = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in r.json().values()}
    total_form4s = 0
    for ticker in tickers:
        cik = tickers_map.get(ticker)
        if not cik:
            continue
        r2 = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=HEADERS, timeout=20)
        data = r2.json()
        forms = data["filings"]["recent"]["form"]
        total_form4s += forms.count("4")
    elapsed = time.perf_counter() - t0
    return elapsed, total_form4s, len(tickers)


# ─── main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    N = 50
    print(f"Profiling on {TARGET_DATE}, N={N} filings each\n")

    print("B: async httpx + lxml ...")
    elapsed, rows, total = asyncio.run(approach_b_async(N))
    print(f"   {elapsed:.2f}s | {rows} purchase rows | {total} filings | {total/elapsed:.1f} filings/s\n")

    print("C: EDGAR EFTS search API ...")
    elapsed, hits, total = approach_c_efts(N)
    print(f"   {elapsed:.2f}s | {hits} hits returned | {total} total on date\n")

    print("D: submissions JSON (10 tickers) ...")
    elapsed, form4s, n_tickers = approach_d_submissions()
    print(f"   {elapsed:.2f}s | {form4s} Form 4s found across {n_tickers} tickers\n")

    print("A: edgartools (baseline, N=50) ...")
    elapsed, rows, total = approach_a_edgartools(N)
    print(f"   {elapsed:.2f}s | {rows} purchase rows | {total} total | {N/elapsed:.1f} filings/s\n")

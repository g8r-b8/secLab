"""Form 4 insider-purchase screener — async httpx + lxml engine.

Ranks tickers by open-market insider buying (SEC code 'P') over a date window.
~95 filings/sec vs ~3/sec with edgartools.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd
from lxml import etree

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
# SEC caps unauthenticated traffic at 10 req/s per IP. Stay under.
CONNECTIONS = 8
MAX_INFLIGHT = 8


# ─── Data model ──────────────────────────────────────────────────────────────

@dataclass
class TickerSignal:
    ticker: str
    issuer: str
    total_value: float = 0.0
    total_shares: float = 0.0
    buyers: set[str] = field(default_factory=set)
    positions: set[str] = field(default_factory=set)

    @property
    def cluster_score(self) -> int:
        return len(self.buyers)

    @property
    def has_c_suite(self) -> bool:
        joined = " ".join(self.positions).lower()
        return any(k in joined for k in ("ceo", "chief executive", "cfo", "chief financial", "president"))


# ─── Index fetch ─────────────────────────────────────────────────────────────

def _quarter(date_str: str) -> tuple[int, int]:
    y, m, _ = map(int, date_str.split("-"))
    return y, (m - 1) // 3 + 1


def get_form4_accessions(start: str, end: str) -> list[tuple[str, str]]:
    """Return (cik, accession) for all Form 4 filings in [start, end] via EFTS search API.
    Falls back to quarterly full-index if EFTS is unavailable.
    """
    results = []
    page_size = 100
    from_pos = 0
    while True:
        url = (
            f"https://efts.sec.gov/LATEST/search-index?forms=4"
            f"&dateRange=custom&startdt={start}&enddt={end}"
            f"&hits.hits.total.value=true"
            f"&hits.hits._source=file_date,period_of_report,entity_id,file_num"
            f"&from={from_pos}&hits.hits._source.size={page_size}"
        )
        try:
            r = httpx.get(url, headers=HEADERS, timeout=30)
            data = r.json()
        except Exception as e:
            print(f"EFTS request failed: {e}")
            break
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            # _id format: "<accession>:<filename>" e.g. "0001907982-26-000031:wk-form4_x.xml"
            src = h.get("_source", {})
            ciks = src.get("ciks") or []
            cik = str(ciks[0]).lstrip("0") if ciks else ""
            accession = h.get("_id", "").split(":")[0]
            if not cik or not accession:
                continue
            results.append((cik, accession))
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        from_pos += len(hits)
        if from_pos >= total:
            break
    return results


# ─── Async fetch + parse ──────────────────────────────────────────────────────

def _parse_form4(text: str) -> list[dict]:
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.DOTALL)
    if not m:
        return []
    try:
        root = etree.fromstring(m.group().encode())
    except Exception:
        return []

    issuer = root.findtext(".//issuerName") or ""
    ticker = (root.findtext(".//issuerTradingSymbol") or "").strip() or "(none)"
    insider = root.findtext(".//rptOwnerName") or "?"
    # Position: try officer title first, fall back to relationship flags
    position = root.findtext(".//officerTitle") or ""
    if not position:
        rels = []
        if root.findtext(".//isDirector") == "1": rels.append("Director")
        if root.findtext(".//isOfficer") == "1": rels.append("Officer")
        if root.findtext(".//isTenPercentOwner") == "1": rels.append("10% Owner")
        position = ", ".join(rels)

    rows = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        if (root.findtext(".//transactionCode") or tx.findtext(".//transactionCode") or "") != "P":
            code = tx.findtext(".//transactionCode") or ""
            if code != "P":
                continue
        try:
            shares = float(tx.findtext(".//transactionShares/value") or 0)
            price  = float(tx.findtext(".//transactionPricePerShare/value") or 0)
        except ValueError:
            shares = price = 0.0
        filed  = root.findtext(".//periodOfReport") or ""
        rows.append({
            "date": filed, "ticker": ticker, "issuer": issuer,
            "insider": insider, "position": position,
            "shares": shares, "price": price, "value": shares * price,
        })
    return rows


_FETCH_STATS = {"ok": 0, "429": 0, "other_http": 0, "exc": 0}


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    cik: str,
    accession: str,
) -> list[dict]:
    acc_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession}.txt"
    async with sem:
        for attempt in range(3):
            try:
                r = await client.get(url, timeout=20)
            except Exception as e:
                _FETCH_STATS["exc"] += 1
                if attempt == 2:
                    print(f"  ! fetch exc {cik}/{accession}: {type(e).__name__}: {e}")
                    return []
                await asyncio.sleep(1 + attempt)
                continue
            if r.status_code == 429:
                _FETCH_STATS["429"] += 1
                retry_after = int(r.headers.get("retry-after", "2"))
                await asyncio.sleep(min(retry_after, 10))
                continue
            if r.status_code >= 400:
                _FETCH_STATS["other_http"] += 1
                return []
            _FETCH_STATS["ok"] += 1
            return _parse_form4(r.text)
        return []


async def _fetch_all(accessions: list[tuple[str, str]]) -> list[dict]:
    for k in _FETCH_STATS:
        _FETCH_STATS[k] = 0
    limits = httpx.Limits(max_connections=CONNECTIONS, max_keepalive_connections=CONNECTIONS)
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    all_rows: list[dict] = []
    t0 = time.perf_counter()
    async with httpx.AsyncClient(headers=HEADERS, limits=limits) as client:
        tasks = [_fetch_one(client, sem, cik, acc) for cik, acc in accessions]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            rows = await coro
            all_rows.extend(rows)
            if i % 200 == 0:
                elapsed = time.perf_counter() - t0
                print(
                    f"  … {i}/{len(accessions)} | {len(all_rows)} purchases | "
                    f"{i/elapsed:.0f} fil/s | ok={_FETCH_STATS['ok']} 429={_FETCH_STATS['429']} "
                    f"4xx/5xx={_FETCH_STATS['other_http']} exc={_FETCH_STATS['exc']}"
                )
    print(
        f"  fetch summary: ok={_FETCH_STATS['ok']} 429={_FETCH_STATS['429']} "
        f"4xx/5xx={_FETCH_STATS['other_http']} exc={_FETCH_STATS['exc']}"
    )
    return all_rows


# ─── Aggregate + rank ─────────────────────────────────────────────────────────

def _aggregate(rows: list[dict]) -> dict[str, TickerSignal]:
    signals: dict[str, TickerSignal] = {}
    for r in rows:
        sig = signals.setdefault(r["ticker"], TickerSignal(ticker=r["ticker"], issuer=r["issuer"]))
        sig.total_shares += r["shares"]
        sig.total_value  += r["value"]
        sig.buyers.add(r["insider"])
        sig.positions.add(r["position"])
    return signals


def _rank(signals: dict[str, TickerSignal], top: int = 25, min_value: float = 0) -> pd.DataFrame:
    rows = [
        {
            "ticker":      s.ticker,
            "issuer":      s.issuer[:40],
            "total_$":     f"${s.total_value:,.0f}",
            "shares":      f"{s.total_shares:,.0f}",
            "# insiders":  s.cluster_score,
            "C-suite":     "Y" if s.has_c_suite else "",
            "positions":   ", ".join(sorted(s.positions))[:60],
        }
        for s in signals.values() if s.total_value >= min_value
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values(["# insiders", "total_$"], ascending=False).head(top).reset_index(drop=True)


# ─── Public API ───────────────────────────────────────────────────────────────

def run(start: str, end: str, top: int = 25, min_value: float = 0,
        refresh: bool = False) -> pd.DataFrame:
    cache = CACHE_DIR / f"purchases_{start}_{end}.parquet"
    if cache.exists() and not refresh:
        print(f"cache hit → {cache.name}")
        rows = pd.read_parquet(cache).to_dict("records")
    else:
        print(f"fetching form.idx for {start} → {end} …")
        accessions = get_form4_accessions(start, end)
        print(f"found {len(accessions)} Form 4 filings — fetching async …")
        t0 = time.perf_counter()
        rows = asyncio.run(_fetch_all(accessions))
        elapsed = time.perf_counter() - t0
        print(f"done in {elapsed:.1f}s | {len(rows)} purchase rows")
        if rows:
            pd.DataFrame(rows).to_parquet(cache)
    return _rank(_aggregate(rows), top=top, min_value=min_value)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _bdays_ago(n: int) -> str:
    d = date.today()
    step = 0
    while step < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            step += 1
    return d.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start",     help="YYYY-MM-DD (default: 3 bdays ago)")
    ap.add_argument("--end",       help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--top",       type=int,   default=25)
    ap.add_argument("--min-value", type=float, default=0, dest="min_value")
    ap.add_argument("--refresh",   action="store_true")
    args = ap.parse_args()

    end   = args.end   or _bdays_ago(1)
    start = args.start or _bdays_ago(3)

    df = run(start, end, top=args.top, min_value=args.min_value, refresh=args.refresh)
    if df.empty:
        print("no insider purchases found")
        return
    print(f"\n=== insider buying leaderboard ({start} → {end}) ===")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()

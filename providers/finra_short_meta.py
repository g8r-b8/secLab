"""FINRA Reg SHO threshold list + weekly short interest.

Both via the FINRA public API (POST JSON, no auth):
  - thresholdList        — daily per-security flag (regShoThresholdFlag = 'Y'
                           means on the threshold list: persistent fails to
                           deliver. Classic squeeze-candidate signal.)
  - equityShortInterest  — bi-monthly short interest per ticker with
                           daysToCover + change vs previous report.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("seclab")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (seclab research)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def _fetch_paged(endpoint: str, filters: dict | None, page_size: int = 5000) -> list[dict]:
    url = f"https://api.finra.org/data/group/otcMarket/name/{endpoint}"
    out: list[dict] = []
    with httpx.Client(headers=_HEADERS, timeout=60) as cli:
        for page in range(200):
            body = {"limit": page_size, "offset": page * page_size}
            if filters:
                body.update(filters)
            try:
                r = cli.post(url, json=body)
            except Exception as e:
                log.warning(f"[finra {endpoint}] page {page} failed: {e}")
                break
            if r.status_code != 200:
                log.warning(f"[finra {endpoint}] page {page} status {r.status_code}")
                break
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                break
            out.extend(rows)
            if len(rows) < page_size:
                break
            time.sleep(0.25)
    return out


def ensure_sho_threshold_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Ingest FINRA Reg SHO threshold-list rows for business days in [start, end]."""
    from data_store import connect
    sd = dt.date.fromisoformat(start)
    ed = dt.date.fromisoformat(end)
    days: list[dt.date] = []
    d = sd
    while d <= ed:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    if not days:
        return {"days": 0, "rows": 0, "skipped": 0}

    con = connect()
    try:
        cached = {row[0] for row in con.execute(
            "SELECT day FROM _sho_threshold_ingested_days"
        ).fetchall()}
        to_fetch = [d for d in days if force or d not in cached]
        if not to_fetch:
            return {"days": 0, "rows": 0, "skipped": len(days), "message": "all cached"}

        log.info(f"[sho_threshold] fetching {len(to_fetch)} days")
        total_rows = 0
        for d in to_fetch:
            # Pull only rows where regShoThresholdFlag='Y' (actual threshold list)
            rows = _fetch_paged(
                "thresholdList",
                {
                    "compareFilters": [
                        {"compareType": "EQUAL", "fieldName": "tradeDate", "fieldValue": d.isoformat()},
                        {"compareType": "EQUAL", "fieldName": "regShoThresholdFlag", "fieldValue": "Y"},
                    ]
                },
            )
            if rows:
                ins = []
                for r in rows:
                    ticker = (r.get("issueSymbolIdentifier") or "").upper()
                    if not ticker:
                        continue
                    ins.append((
                        d,
                        ticker,
                        r.get("marketClassCode") or "",
                        r.get("issueName") or "",
                        r.get("cusip") or "",
                    ))
                if ins:
                    con.executemany(
                        """
                        INSERT INTO reg_sho_threshold (date, ticker, market, issuer, cusip)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        ins,
                    )
                    total_rows += len(ins)
            con.execute(
                "INSERT OR REPLACE INTO _sho_threshold_ingested_days (day, ingested_at) "
                "VALUES (?, CURRENT_TIMESTAMP)",
                [d],
            )
        return {"days": len(to_fetch), "rows": total_rows, "skipped": len(days) - len(to_fetch)}
    finally:
        con.close()


def ensure_short_interest_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Ingest FINRA weekly-short-interest rows settling in [start, end].
    Records are bi-monthly (15th / end-of-month settlement)."""
    from data_store import connect
    con = connect()
    try:
        cached = {row[0] for row in con.execute(
            "SELECT settlement_date FROM _short_interest_ingested_dates"
        ).fetchall()}

        rows = _fetch_paged(
            "equityShortInterest",
            {
                "dateRangeFilters": [
                    {"fieldName": "settlementDate", "startDate": start, "endDate": end}
                ]
            },
        )
        if not rows:
            return {"dates": 0, "rows": 0, "message": "no data"}

        ins = []
        dates_seen: set[dt.date] = set()
        for r in rows:
            ticker = (r.get("issueSymbolIdentifier") or "").upper()
            sd = r.get("settlementDate")
            if not ticker or not sd:
                continue
            try:
                sd_dt = dt.date.fromisoformat(sd)
            except Exception:
                continue
            if not force and sd_dt in cached:
                continue
            dates_seen.add(sd_dt)
            ins.append((
                sd_dt,
                ticker,
                int(r.get("currentShortShareNumber") or 0),
                int(r.get("previousShortShareNumber") or 0),
                float(r.get("changePercent") or 0.0),
                int(r.get("averageShortShareNumber") or 0),
                float(r.get("daysToCoverNumber") or 0.0),
            ))

        if ins:
            con.executemany(
                """
                INSERT INTO short_interest
                    (settlement_date, ticker, shares_short, shares_short_prev,
                     change_pct, avg_daily_volume, days_to_cover)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                ins,
            )
        for d in dates_seen:
            con.execute(
                "INSERT OR REPLACE INTO _short_interest_ingested_dates "
                "(settlement_date, ingested_at) VALUES (?, CURRENT_TIMESTAMP)",
                [d],
            )
        return {"dates": len(dates_seen), "rows": len(ins)}
    finally:
        con.close()

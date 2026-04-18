"""FINRA weekly off-exchange volume — ATS (dark pools) + OTC non-ATS (wholesalers).

Source: https://api.finra.org/data/group/otcMarket/name/weeklySummary

Two distinct `summaryTypeCode`s share the endpoint:
  - ATS_W_SMBL_FIRM — per-ticker per-ATS (dark-pool) weekly volume
  - OTC_W_SMBL_FIRM — per-ticker per-wholesaler weekly volume (Citadel
    Securities, Virtu, Jane Street, etc.) — a.k.a. "retail order flow"

Key lessons from earlier exploration:
  - Must use POST (not GET) for filters to apply. GET returns a fixed
    slice regardless of query params.
  - dateRangeFilters uses {fieldName, startDate, endDate}.
  - Data publishes with ~2-3 week lag.
  - No auth required.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("seclab")

FINRA_API_URL = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (seclab research)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


# Venue-type registry: maps our short venue_type tag to FINRA's
# summaryTypeCode + the DB tables that hold the result.
_VENUES = {
    "ats": {
        "summary_type": "ATS_W_SMBL_FIRM",
        "table": "dark_pool_weekly",
        "ingested_table": "_dark_pool_ingested_weeks",
        "log_tag": "dark_pool",
    },
    "non_ats": {
        "summary_type": "OTC_W_SMBL_FIRM",
        "table": "non_ats_weekly",
        "ingested_table": "_non_ats_ingested_weeks",
        "log_tag": "non_ats",
    },
}


def _fetch_weekly(
    summary_type: str,
    start: str,
    end: str,
    *,
    page_size: int = 5000,
    max_pages: int = 200,
    throttle_s: float = 0.25,
) -> list[dict[str, Any]]:
    """Fetch every row matching summaryTypeCode + date range, paginated."""
    body = {
        "limit": page_size,
        "offset": 0,
        "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": summary_type},
        ],
        "dateRangeFilters": [
            {"fieldName": "weekStartDate", "startDate": start, "endDate": end},
        ],
    }

    out: list[dict[str, Any]] = []
    with httpx.Client(headers=_HEADERS, timeout=60) as cli:
        for page in range(max_pages):
            body["offset"] = page * page_size
            try:
                r = cli.post(FINRA_API_URL, json=body)
            except Exception as e:
                log.warning(f"[finra {summary_type}] page {page} POST failed: {e}")
                break
            if r.status_code != 200:
                log.warning(f"[finra {summary_type}] page {page} status {r.status_code}")
                break
            rows = r.json()
            if not isinstance(rows, list):
                log.warning(f"[finra {summary_type}] unexpected response: {type(rows)}")
                break
            if not rows:
                break
            out.extend(rows)
            if len(rows) < page_size:
                break
            if throttle_s > 0:
                time.sleep(throttle_s)
    return out


def _ensure_venue_window(
    venue_type: str,
    start: str,
    end: str,
    force: bool = False,
) -> dict[str, Any]:
    """Ingest a date range for a given venue_type into its dedicated table.

    Idempotent: weeks already in the ingested-log are skipped unless force=True.
    """
    if venue_type not in _VENUES:
        raise ValueError(f"unknown venue_type {venue_type}")
    cfg = _VENUES[venue_type]
    summary_type = cfg["summary_type"]
    table = cfg["table"]
    ingested_tbl = cfg["ingested_table"]
    tag = cfg["log_tag"]

    from data_store import connect  # late import: avoid circular
    sd = dt.date.fromisoformat(start)
    ed = dt.date.fromisoformat(end)

    # Every Monday in [sd, ed]
    first_mon = sd - dt.timedelta(days=sd.weekday())
    weeks: list[dt.date] = []
    d = first_mon
    while d <= ed:
        weeks.append(d)
        d += dt.timedelta(days=7)
    if not weeks:
        return {"weeks": 0, "rows": 0, "skipped": 0}

    con = connect()
    try:
        cached = {row[0] for row in con.execute(
            f"SELECT week_start FROM {ingested_tbl}"
        ).fetchall()}
        weeks_to_fetch = [w for w in weeks if force or w not in cached]

        if not weeks_to_fetch:
            return {"weeks": 0, "rows": 0, "skipped": len(weeks), "message": "all cached"}

        log.info(
            f"[{tag}] fetching {len(weeks_to_fetch)} weeks "
            f"({weeks_to_fetch[0].isoformat()} -> {weeks_to_fetch[-1].isoformat()}), "
            f"skipping {len(weeks) - len(weeks_to_fetch)} cached"
        )
        # Group contiguous weeks into ranges
        ranges: list[tuple[dt.date, dt.date]] = []
        cur_start = weeks_to_fetch[0]
        cur_end = weeks_to_fetch[0]
        for w in weeks_to_fetch[1:]:
            if (w - cur_end).days == 7:
                cur_end = w
            else:
                ranges.append((cur_start, cur_end))
                cur_start = cur_end = w
        ranges.append((cur_start, cur_end))

        total_rows = 0
        for rs, re in ranges:
            re_inclusive = re + dt.timedelta(days=6)
            log.info(f"[{tag}] range {rs} -> {re_inclusive}")
            rows = _fetch_weekly(summary_type, rs.isoformat(), re_inclusive.isoformat())
            log.info(f"[{tag}] fetched {len(rows)} rows")
            total_rows += len(rows)

            insert_rows = []
            for r in rows:
                ticker = r.get("issueSymbolIdentifier")
                week = r.get("weekStartDate")
                if not ticker or not week:
                    continue
                # MPID is None for the "De Minimis Firms" aggregate rows that
                # FINRA reports for small wholesalers. Fall back to CRD+name
                # so we still capture that flow.
                mpid = r.get("MPID")
                if not mpid:
                    crd = r.get("firmCRDNumber")
                    mpid = f"CRD_{crd}" if crd is not None else "UNKNOWN"
                try:
                    week_dt = dt.date.fromisoformat(week)
                except Exception:
                    continue
                insert_rows.append((
                    week_dt,
                    ticker.upper(),
                    str(mpid).upper(),
                    r.get("marketParticipantName") or "",
                    r.get("tierIdentifier") or "",
                    int(r.get("totalWeeklyShareQuantity") or 0),
                    int(r.get("totalWeeklyTradeCount") or 0),
                    float(r.get("totalNotionalSum") or 0.0),
                ))

            if insert_rows:
                con.executemany(
                    f"""
                    INSERT INTO {table}
                        (week_start, ticker, mpid, ats_name, tier, shares, trades, notional)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    insert_rows,
                )
            # Mark every Monday in the range as ingested even if 0 rows.
            w = rs
            while w <= re:
                con.execute(
                    f"INSERT OR REPLACE INTO {ingested_tbl} (week_start, ingested_at) "
                    "VALUES (?, CURRENT_TIMESTAMP)",
                    [w],
                )
                w += dt.timedelta(days=7)

        return {
            "weeks": len(weeks_to_fetch),
            "rows": total_rows,
            "skipped": len(weeks) - len(weeks_to_fetch),
        }
    finally:
        con.close()


# Public API — preserved names so existing callers don't break.

def ensure_dark_pool_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Ingest ATS dark-pool weekly data."""
    return _ensure_venue_window("ats", start, end, force=force)


def ensure_non_ats_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Ingest OTC non-ATS (wholesaler / retail flow) weekly data."""
    return _ensure_venue_window("non_ats", start, end, force=force)

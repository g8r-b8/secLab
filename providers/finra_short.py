"""FINRA daily consolidated short-sale volume (Reg SHO file).

URL pattern: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
Format: pipe-delimited, header:
  Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

Files publish end-of-day the next morning. Weekends/holidays: no file (404).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

log = logging.getLogger("seclab")

FILE_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"

_HEADERS = {"User-Agent": "Mozilla/5.0 (seclab research)"}


def _parse_line(line: str) -> tuple | None:
    """Parse one data row. Returns None on header / trailer / malformed."""
    parts = line.strip().split("|")
    if len(parts) < 5:
        return None
    if parts[0] == "Date":
        return None
    try:
        d_raw = parts[0]
        d = dt.date(int(d_raw[:4]), int(d_raw[4:6]), int(d_raw[6:8]))
        ticker = parts[1].strip()
        if not ticker:
            return None
        short_vol = int(float(parts[2] or 0))
        short_exempt = int(float(parts[3] or 0))
        total_vol = int(float(parts[4] or 0))
        market = parts[5] if len(parts) > 5 else ""
        return (d, ticker.upper(), short_vol, short_exempt, total_vol, market)
    except Exception:
        return None


def fetch_day(day: dt.date) -> list[tuple]:
    """Fetch + parse one day's CNMS file. Returns [] if not available."""
    url = FILE_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
    try:
        with httpx.Client(headers=_HEADERS, timeout=30) as cli:
            r = cli.get(url)
    except Exception as e:
        log.warning(f"[short_sale] {day} fetch failed: {e}")
        return []
    if r.status_code == 404:
        return []  # weekend / holiday / not yet published
    if r.status_code != 200:
        log.warning(f"[short_sale] {day} status {r.status_code}")
        return []
    out: list[tuple] = []
    for line in r.text.splitlines():
        row = _parse_line(line)
        if row is not None:
            out.append(row)
    return out


def ensure_short_sale_window(
    start: str,
    end: str,
    force: bool = False,
) -> dict[str, Any]:
    """Pull every business day in [start, end] that isn't already ingested."""
    from data_store import connect  # late import: avoid circular
    sd = dt.date.fromisoformat(start)
    ed = dt.date.fromisoformat(end)

    # Only US business days — skip weekends
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
            "SELECT day FROM _short_sale_ingested_days"
        ).fetchall()}
        to_fetch = [d for d in days if force or d not in cached]
        if not to_fetch:
            return {"days": 0, "rows": 0, "skipped": len(days), "message": "all cached"}

        log.info(f"[short_sale] fetching {len(to_fetch)} days ({to_fetch[0]} -> {to_fetch[-1]})")
        total_rows = 0
        for d in to_fetch:
            rows = fetch_day(d)
            if rows:
                con.executemany(
                    """
                    INSERT INTO short_sale_daily
                        (date, ticker, short_volume, short_exempt, total_volume, market)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    rows,
                )
                total_rows += len(rows)
            con.execute(
                "INSERT OR REPLACE INTO _short_sale_ingested_days (day, ingested_at) "
                "VALUES (?, CURRENT_TIMESTAMP)",
                [d],
            )
        log.info(f"[short_sale] inserted {total_rows} rows across {len(to_fetch)} days")
        return {
            "days": len(to_fetch),
            "rows": total_rows,
            "skipped": len(days) - len(to_fetch),
        }
    finally:
        con.close()

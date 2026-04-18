"""SEC Fails-to-Deliver (CNS fails) bi-monthly CSV ingest.

SEC publishes a ZIP per half-month at:
  https://www.sec.gov/files/data/fails-deliver-data/cnsfails{YYYYMM}{a|b}.zip

  'a' = first half (settlement dates roughly 1st-15th)
  'b' = second half (16th-end)

Inside each ZIP: a single pipe-delimited text file with columns:
  SETTLEMENT DATE | CUSIP | SYMBOL | QUANTITY (FAILS) | DESCRIPTION | PRICE

Lag: typically published ~2-3 weeks after the period ends.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile
from typing import Any

import httpx

log = logging.getLogger("seclab")

FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{key}.zip"

_HEADERS = {"User-Agent": "wingatejones@gmail.com (seclab research)"}


def _file_keys_for_window(start: dt.date, end: dt.date) -> list[str]:
    """All half-month keys like '202602a', '202602b' covering [start, end]."""
    keys: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        keys.append(f"{y:04d}{m:02d}a")
        keys.append(f"{y:04d}{m:02d}b")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def fetch_file(key: str) -> list[tuple] | None:
    """Download + parse one FTD ZIP. Returns None if not yet published (404)."""
    url = FTD_URL.format(key=key)
    try:
        with httpx.Client(headers=_HEADERS, timeout=60) as cli:
            r = cli.get(url)
    except Exception as e:
        log.warning(f"[ftd] {key} fetch failed: {e}")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        log.warning(f"[ftd] {key} status {r.status_code}")
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next((n for n in z.namelist() if not n.endswith("/")), None)
            if not name:
                return None
            raw = z.read(name).decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"[ftd] {key} unzip failed: {e}")
        return None

    rows: list[tuple] = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        if parts[0] == "SETTLEMENT DATE":
            continue
        try:
            d_raw = parts[0]
            if len(d_raw) != 8 or not d_raw.isdigit():
                continue
            d = dt.date(int(d_raw[:4]), int(d_raw[4:6]), int(d_raw[6:8]))
            cusip = (parts[1] or "").strip()
            if not cusip:
                continue
            ticker = (parts[2] or "").strip().upper() or None
            qty = int(float(parts[3] or 0))
            desc = (parts[4] or "").strip() if len(parts) > 4 else ""
            price = float(parts[5] or 0) if len(parts) > 5 else 0.0
            rows.append((d, cusip, ticker, qty, desc, price))
        except Exception:
            continue
    return rows


def ensure_ftd_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Ingest every half-month file covering [start, end] (if not cached)."""
    from data_store import connect
    sd = dt.date.fromisoformat(start)
    ed = dt.date.fromisoformat(end)
    keys = _file_keys_for_window(sd, ed)

    con = connect()
    try:
        cached = {row[0] for row in con.execute(
            "SELECT file_key FROM _ftd_ingested_files"
        ).fetchall()}
        to_fetch = [k for k in keys if force or k not in cached]
        if not to_fetch:
            return {"files": 0, "rows": 0, "skipped": len(keys), "message": "all cached"}

        log.info(f"[ftd] fetching {len(to_fetch)} half-month files")
        total_rows = 0
        hit_files = 0
        for k in to_fetch:
            rows = fetch_file(k)
            if rows is None:
                # 404 = not yet published; don't mark as ingested so a later
                # refresh will retry.
                continue
            hit_files += 1
            if rows:
                con.executemany(
                    """
                    INSERT INTO fails_to_deliver
                        (settlement_date, cusip, ticker, quantity, description, price)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    rows,
                )
                total_rows += len(rows)
            con.execute(
                "INSERT OR REPLACE INTO _ftd_ingested_files (file_key, ingested_at) "
                "VALUES (?, CURRENT_TIMESTAMP)",
                [k],
            )
        log.info(f"[ftd] ingested {total_rows} rows across {hit_files} files")
        return {
            "files": hit_files,
            "rows": total_rows,
            "skipped": len(keys) - len(to_fetch),
            "requested": len(to_fetch),
        }
    finally:
        con.close()

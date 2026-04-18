"""IPO tracker — S-1 registrations + 424B4 pricings via EDGAR EFTS.

Form codes:
  S-1, S-1/A          — initial registration + amendments
  424B1, 424B3, 424B4 — final/supplementary prospectus; 424B4 dates pricing
  8-A12B, 8-A12G      — exchange listing (often same day as 424B4)

Re-uses the generic `fetch_efts` helper so ingest + dedup parity with
the other simple-filing providers.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from providers.simple_filings import GenericFiling, fetch_efts

log = logging.getLogger("seclab")

IPO_FORMS = ["S-1", "S-1/A", "424B1", "424B3", "424B4", "8-A12B", "8-A12G"]


def fetch_ipos(start: str, end: str) -> list[GenericFiling]:
    return fetch_efts(IPO_FORMS, start, end, log_prefix="ipo")


def ensure_ipo_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
    """Scrape EFTS for every business day in [start, end] not already ingested."""
    from data_store import connect  # late import
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
            "SELECT day FROM _ipo_ingested_days"
        ).fetchall()}
        to_fetch = sorted(d for d in days if force or d not in cached)
        if not to_fetch:
            return {"days": 0, "rows": 0, "skipped": len(days), "message": "all cached"}

        # EFTS supports ranges. Split into contiguous blocks to minimize calls.
        ranges: list[tuple[dt.date, dt.date]] = []
        cur_start = to_fetch[0]
        prev = to_fetch[0]
        for d in to_fetch[1:]:
            if (d - prev).days <= 4:  # tolerate small gaps
                prev = d
            else:
                ranges.append((cur_start, prev))
                cur_start = prev = d
        ranges.append((cur_start, prev))

        total_rows = 0
        for rs, re in ranges:
            log.info(f"[ipo] scraping {rs} -> {re}")
            hits = fetch_ipos(rs.isoformat(), re.isoformat())
            if hits:
                rows_ins = []
                for f in hits:
                    try:
                        fd = dt.date.fromisoformat(f.filing_date) if f.filing_date else None
                    except Exception:
                        fd = None
                    if fd is None:
                        continue
                    rows_ins.append((
                        f.accession, f.form, fd,
                        f.issuer_cik, f.issuer_name, f.issuer_ticker, f.primary_doc,
                    ))
                if rows_ins:
                    con.executemany(
                        """
                        INSERT INTO ipo_filings
                            (accession, form, filing_date, issuer_cik, issuer_name,
                             issuer_ticker, primary_doc)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        rows_ins,
                    )
                    total_rows += len(rows_ins)
            # mark every day in this block as ingested
            d = rs
            while d <= re:
                con.execute(
                    "INSERT OR REPLACE INTO _ipo_ingested_days (day, ingested_at) "
                    "VALUES (?, CURRENT_TIMESTAMP)",
                    [d],
                )
                d += dt.timedelta(days=1)

        return {
            "days": len(to_fetch),
            "rows": total_rows,
            "skipped": len(days) - len(to_fetch),
        }
    finally:
        con.close()

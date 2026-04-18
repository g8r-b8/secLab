"""Merger proxies + going-private filings via EDGAR EFTS.

Forms:
  PREM14A  — preliminary merger proxy (first public signal of a deal)
  DEFM14A  — definitive merger proxy (vote date set, deal terms final)
  SC 13E-3 — going-private transaction (management buyout / take-private)
  DEFA14A  — additional proxy materials (updates to pending deal)
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from providers.simple_filings import GenericFiling, fetch_efts

log = logging.getLogger("seclab")

MERGER_FORMS = ["PREM14A", "PREM14A/A", "DEFM14A", "DEFM14A/A", "SC 13E-3", "SC 13E-3/A"]


def fetch_mergers(start: str, end: str) -> list[GenericFiling]:
    return fetch_efts(MERGER_FORMS, start, end, log_prefix="merger")


def ensure_merger_window(start: str, end: str, force: bool = False) -> dict[str, Any]:
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
            "SELECT day FROM _merger_proxies_ingested_days"
        ).fetchall()}
        to_fetch = sorted(d for d in days if force or d not in cached)
        if not to_fetch:
            return {"days": 0, "rows": 0, "skipped": len(days), "message": "all cached"}

        # Group contiguous blocks
        ranges: list[tuple[dt.date, dt.date]] = []
        cur_start = to_fetch[0]
        prev = to_fetch[0]
        for d in to_fetch[1:]:
            if (d - prev).days <= 4:
                prev = d
            else:
                ranges.append((cur_start, prev))
                cur_start = prev = d
        ranges.append((cur_start, prev))

        total_rows = 0
        for rs, re in ranges:
            log.info(f"[merger] scraping {rs} -> {re}")
            hits = fetch_mergers(rs.isoformat(), re.isoformat())
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
                        f.issuer_cik, f.issuer_name, f.issuer_ticker,
                        f.filer_cik, f.filer_name, f.primary_doc,
                    ))
                if rows_ins:
                    con.executemany(
                        """
                        INSERT INTO merger_proxies
                            (accession, form, filing_date, issuer_cik, issuer_name,
                             issuer_ticker, filer_cik, filer_name, primary_doc)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        rows_ins,
                    )
                    total_rows += len(rows_ins)
            d = rs
            while d <= re:
                con.execute(
                    "INSERT OR REPLACE INTO _merger_proxies_ingested_days (day, ingested_at) "
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

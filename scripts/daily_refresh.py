#!/usr/bin/env python
"""Daily refresh: pull fresh SEC data + advance all active paper portfolios.

Intended to run once a day (via cron / launchd / manual). Safe to call
more often — all `ensure_*_window` helpers are idempotent and the paper
advance function overwrites with the latest deterministic replay.

Usage:
    python scripts/daily_refresh.py                   # last 30 days
    python scripts/daily_refresh.py --days 60         # wider backfill
    python scripts/daily_refresh.py --skip-scrape     # only DERA + paper
    python scripts/daily_refresh.py --skip-dera       # only scrape + paper

Scheduling example (macOS launchd or linux cron):
    # every weekday at 7pm local, after US close
    0 19 * * 1-5  cd /path/to/secLab && .venv/bin/python scripts/daily_refresh.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

# Ensure repo root is on sys.path when run directly.
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from data_store import (  # noqa: E402
    ensure_insider_quarters,
    ensure_fundamentals_quarters,
    ensure_form144_window,
    ensure_thirteen_d_window,
    ensure_13g_window,
    ensure_form3_window,
    ensure_formD_window,
    ensure_delistings_window,
    ensure_foreign_filings_window,
    ensure_secondaries_window,
    ensure_tender_offers_window,
    ensure_proxy_contests_window,
    ensure_late_filings_window,
    ensure_8k_window,
)
from backtester import forward as paper  # noqa: E402


logger = logging.getLogger("seclab.refresh")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)


def _step(label: str, fn, *args, **kwargs):
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        logger.info(f"  [{label}] ok in {time.time() - t0:.1f}s — {result}")
        return result
    except Exception as e:
        logger.warning(f"  [{label}] failed in {time.time() - t0:.1f}s: {e}")
        return None


def refresh_dera(today: str) -> None:
    """Pull the current + previous quarter of bulk DERA data if not yet ingested."""
    today_dt = dt.date.fromisoformat(today)
    # Go back ~180 days so we cover both the just-closed quarter and the one before.
    start = (today_dt - dt.timedelta(days=180)).isoformat()
    logger.info(f"DERA quarterly bulk ({start} → {today}) — skips already-ingested quarters")
    _step("insider-dera", ensure_insider_quarters, start, today)
    _step("fundamentals-dera", ensure_fundamentals_quarters, start, today)


def refresh_scraped(today: str, days: int) -> None:
    """Refresh live-scraped EFTS windows for the last `days` days."""
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=days)).isoformat()
    logger.info(f"EFTS live-scraped windows: {start} → {today}")
    _step("form144", ensure_form144_window, start, today)
    _step("13D", ensure_thirteen_d_window, start, today)
    _step("13G", ensure_13g_window, start, today)
    _step("form3", ensure_form3_window, start, today)
    _step("formD", ensure_formD_window, start, today)
    _step("delistings", ensure_delistings_window, start, today)
    _step("foreign-filings", ensure_foreign_filings_window, start, today)
    _step("secondaries", ensure_secondaries_window, start, today)
    _step("tender-offers", ensure_tender_offers_window, start, today)
    _step("proxy-contests", ensure_proxy_contests_window, start, today)
    _step("late-filings", ensure_late_filings_window, start, today)
    _step("8-K", ensure_8k_window, start, today)


def advance_papers(today: str) -> None:
    """Advance all active paper portfolios to today."""
    logger.info(f"Advancing paper portfolios → {today}")
    results = paper.advance_all(as_of=today)
    for r in results:
        status = r.get("status", "?")
        name = r.get("name", "?")
        if status == "ok":
            logger.info(f"  [paper] {name}: advanced")
        else:
            logger.warning(f"  [paper] {name}: {status} — {r.get('error', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="secLab daily refresh")
    parser.add_argument("--days", type=int, default=30, help="Scrape window in days (default 30)")
    parser.add_argument("--as-of", type=str, default=None, help="ISO date (default: today)")
    parser.add_argument("--skip-dera", action="store_true", help="Skip DERA quarterly ingest")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip EFTS live scrapes")
    parser.add_argument("--skip-paper", action="store_true", help="Skip paper-portfolio advance")
    args = parser.parse_args()

    today = args.as_of or dt.date.today().isoformat()
    logger.info(f"=== secLab daily refresh · as_of={today} · window={args.days}d ===")
    t0 = time.time()

    if not args.skip_dera:
        refresh_dera(today)
    if not args.skip_scrape:
        refresh_scraped(today, args.days)
    if not args.skip_paper:
        advance_papers(today)

    logger.info(f"=== done in {time.time() - t0:.1f}s ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

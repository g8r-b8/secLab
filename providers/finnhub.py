"""Finnhub fallback for insider transactions not yet in DERA.

DERA publishes insider data quarterly. While a quarter is in progress, the only
way to see fresh Form 4s is a live source. Finnhub's free tier gives us
/stock/insider-transactions per-symbol with shares, price, date, name.

The free tier is per-symbol — we'd need a universe of tickers to fan out
across. For now this exposes a single-ticker lookup and a small helper to
aggregate a seed-universe. Full universe scan is out of scope for the free
tier; sec-api.io or a paid Finnhub plan is needed for that.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
FINNHUB_BASE = "https://finnhub.io/api/v1"


def enabled() -> bool:
    return bool(FINNHUB_KEY)


def get_insider_transactions(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    """Fetch insider transactions for a single symbol in a date window.

    Normalized output matches data_store.get_insider_purchases().transactions shape:
      date, ticker, issuer, cik, shares, price, value, accession
    """
    if not FINNHUB_KEY:
        return []
    params = {"symbol": symbol, "from": start, "to": end, "token": FINNHUB_KEY}
    with httpx.Client(timeout=20) as client:
        r = client.get(f"{FINNHUB_BASE}/stock/insider-transactions", params=params)
        r.raise_for_status()
        data = r.json()

    out: list[dict[str, Any]] = []
    for row in data.get("data", []):
        change = row.get("change", 0) or 0
        price = row.get("transactionPrice", 0) or 0
        if change <= 0 or price <= 0:
            continue  # skip sells, gifts, grants
        out.append(
            {
                "date": row.get("transactionDate"),
                "ticker": data.get("symbol") or symbol,
                "issuer": row.get("name") or "",
                "cik": "",
                "shares": float(change),
                "price": float(price),
                "value": float(change) * float(price),
                "accession": row.get("filingDate") or "",
                "insider_name": row.get("name") or "",
                "source": "finnhub",
            }
        )
    return out


def get_current_quarter_purchases(symbols: list[str], start: str, end: str) -> list[dict[str, Any]]:
    """Aggregate purchases across a seed universe.

    Finnhub free tier is per-symbol — callers must provide the tickers to
    scan. Suitable seed universes: S&P 500, Russell 2000, a user watchlist.
    """
    if not FINNHUB_KEY:
        return []
    all_rows: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            all_rows.extend(get_insider_transactions(sym, start, end))
        except Exception:
            continue
    return all_rows

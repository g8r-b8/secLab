"""Live IEX equity quotes + trades via Alpaca's free data API.

Alpaca's free tier includes the IEX data feed: real-time top-of-book quotes
and trade reports from the IEX exchange (covers all US-listed equities, but
only trades routed to IEX venue — roughly 2-3% of total market volume).

Signup: https://alpaca.markets/ — no credit card, email only.

Env vars (both required to activate):
  ALPACA_API_KEY_ID
  ALPACA_API_SECRET

Gracefully no-ops if either is missing; the UI surfaces signup instructions.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

BASE = "https://data.alpaca.markets"


def _creds() -> tuple[str, str] | None:
    kid = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not kid or not secret:
        return None
    return kid, secret


def enabled() -> bool:
    return _creds() is not None


def _headers() -> dict[str, str]:
    creds = _creds() or ("", "")
    return {
        "APCA-API-KEY-ID": creds[0],
        "APCA-API-SECRET-KEY": creds[1],
        "Accept": "application/json",
    }


def get_latest_quote(ticker: str) -> dict[str, Any]:
    """Latest top-of-book bid/ask from IEX for `ticker`."""
    if not enabled():
        return {"error": "alpaca_not_configured"}
    url = f"{BASE}/v2/stocks/{ticker.upper()}/quotes/latest"
    try:
        r = httpx.get(url, headers=_headers(), params={"feed": "iex"}, timeout=10)
    except Exception as e:
        return {"error": str(e)}
    if r.status_code != 200:
        return {"error": f"status {r.status_code}", "body": r.text[:200]}
    d = r.json() or {}
    q = d.get("quote") or {}
    return {
        "symbol": d.get("symbol") or ticker.upper(),
        "timestamp": q.get("t"),
        "ask_price": q.get("ap"),
        "ask_size": q.get("as"),
        "bid_price": q.get("bp"),
        "bid_size": q.get("bs"),
        "ask_exchange": q.get("ax"),
        "bid_exchange": q.get("bx"),
        "conditions": q.get("c"),
    }


def get_latest_trade(ticker: str) -> dict[str, Any]:
    """Most recent trade on IEX for `ticker`."""
    if not enabled():
        return {"error": "alpaca_not_configured"}
    url = f"{BASE}/v2/stocks/{ticker.upper()}/trades/latest"
    try:
        r = httpx.get(url, headers=_headers(), params={"feed": "iex"}, timeout=10)
    except Exception as e:
        return {"error": str(e)}
    if r.status_code != 200:
        return {"error": f"status {r.status_code}", "body": r.text[:200]}
    d = r.json() or {}
    t = d.get("trade") or {}
    return {
        "symbol": d.get("symbol") or ticker.upper(),
        "timestamp": t.get("t"),
        "price": t.get("p"),
        "size": t.get("s"),
        "exchange": t.get("x"),
        "conditions": t.get("c"),
    }


def get_snapshot(ticker: str) -> dict[str, Any]:
    """Consolidated view: latest quote + latest trade + daily bar."""
    if not enabled():
        return {"error": "alpaca_not_configured"}
    url = f"{BASE}/v2/stocks/{ticker.upper()}/snapshot"
    try:
        r = httpx.get(url, headers=_headers(), params={"feed": "iex"}, timeout=10)
    except Exception as e:
        return {"error": str(e)}
    if r.status_code != 200:
        return {"error": f"status {r.status_code}", "body": r.text[:200]}
    d = r.json() or {}
    q = d.get("latestQuote") or {}
    t = d.get("latestTrade") or {}
    bar = d.get("dailyBar") or {}
    prev = d.get("prevDailyBar") or {}
    # daily_change is based on last trade vs previous close
    last = t.get("p")
    prev_close = prev.get("c")
    day_change_pct = None
    if last is not None and prev_close:
        try:
            day_change_pct = (float(last) / float(prev_close)) - 1.0
        except Exception:
            pass
    return {
        "symbol": ticker.upper(),
        "quote": {
            "timestamp": q.get("t"),
            "ask_price": q.get("ap"),
            "ask_size": q.get("as"),
            "bid_price": q.get("bp"),
            "bid_size": q.get("bs"),
            "spread": (
                (q.get("ap") - q.get("bp"))
                if (q.get("ap") is not None and q.get("bp") is not None)
                else None
            ),
        },
        "trade": {
            "timestamp": t.get("t"),
            "price": t.get("p"),
            "size": t.get("s"),
            "exchange": t.get("x"),
        },
        "day_bar": {
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
            "vwap": bar.get("vw"),
        },
        "prev_close": prev_close,
        "day_change_pct": day_change_pct,
    }


def get_snapshots(tickers: list[str]) -> dict[str, Any]:
    """Batch snapshot fetch — one HTTP call for up to ~200 tickers."""
    if not enabled():
        return {"error": "alpaca_not_configured"}
    syms = ",".join(t.upper() for t in tickers if t)
    if not syms:
        return {}
    url = f"{BASE}/v2/stocks/snapshots"
    try:
        r = httpx.get(
            url, headers=_headers(),
            params={"symbols": syms, "feed": "iex"},
            timeout=15,
        )
    except Exception as e:
        return {"error": str(e)}
    if r.status_code != 200:
        return {"error": f"status {r.status_code}", "body": r.text[:200]}
    raw = r.json() or {}
    out: dict[str, Any] = {}
    for sym, d in raw.items():
        q = d.get("latestQuote") or {}
        t = d.get("latestTrade") or {}
        prev = d.get("prevDailyBar") or {}
        last = t.get("p")
        prev_close = prev.get("c")
        day_change_pct = None
        if last is not None and prev_close:
            try:
                day_change_pct = (float(last) / float(prev_close)) - 1.0
            except Exception:
                pass
        out[sym] = {
            "ask_price": q.get("ap"),
            "ask_size": q.get("as"),
            "bid_price": q.get("bp"),
            "bid_size": q.get("bs"),
            "spread": (
                (q.get("ap") - q.get("bp"))
                if (q.get("ap") is not None and q.get("bp") is not None)
                else None
            ),
            "last_price": t.get("p"),
            "last_size": t.get("s"),
            "last_timestamp": t.get("t"),
            "quote_timestamp": q.get("t"),
            "prev_close": prev_close,
            "day_change_pct": day_change_pct,
        }
    return out

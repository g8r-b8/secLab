"""EOD price provider — yfinance primary, Tiingo fallback, persistent disk cache.

Fallback chain:
  1. Parquet disk cache at .cache/prices/
  2. yfinance
  3. Tiingo (if TIINGO_API_KEY env var is set — free tier, 30s signup)
  4. Raise `RateLimited` if all providers fail with throttle errors.

Yahoo bans IPs aggressively; Tiingo gives us ~500 req/hr and survives while
Yahoo cools off. Successful fetches are always written to the disk cache so a
single pull feeds every subsequent page visit.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "").strip()
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

CACHE_DIR = Path(__file__).parent.parent / ".cache" / "prices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INFO_CACHE_DIR = Path(__file__).parent.parent / ".cache" / "prices_info"
INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class RateLimited(Exception):
    """Raised when yfinance hits Yahoo's per-IP throttle."""


def _cache_path(ticker: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{ticker}_{start}_{end}.parquet"


def _info_cache_path(ticker: str) -> Path:
    return INFO_CACHE_DIR / f"{ticker}.json"


def _is_rate_limit_error(e: Exception) -> bool:
    # yfinance raises YFRateLimitError, plus raw 429s can surface in other ways.
    msg = str(e).lower()
    return (
        type(e).__name__ == "YFRateLimitError"
        or "rate limit" in msg
        or "too many requests" in msg
        or "429" in msg
    )


def _normalize_ticker_variants(ticker: str) -> list[str]:
    """Yahoo expects `BRK-B`, SEC filings often show `BRK.B` or `BRK/B`.

    Returns an ordered list of variants to try. First non-empty result wins.
    """
    t = ticker.upper().strip()
    variants = [t]
    if "." in t:
        variants.append(t.replace(".", "-"))
    if "/" in t:
        variants.append(t.replace("/", "-"))
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _fetch_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Try ticker variants in order until one returns non-empty data."""
    last_exc: Exception | None = None
    for variant in _normalize_ticker_variants(ticker):
        try:
            t = yf.Ticker(variant)
            df = t.history(start=start, end=end, auto_adjust=False, actions=False)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            if _is_rate_limit_error(e):
                raise RateLimited(str(e)) from e
            last_exc = e
    if last_exc is not None:
        # No rate limit, but an exception on the last variant — surface it
        raise last_exc
    return pd.DataFrame()


def _fetch_tiingo(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Tiingo fallback. Free tier needs TIINGO_API_KEY env var."""
    if not TIINGO_API_KEY:
        return pd.DataFrame()
    for variant in _normalize_ticker_variants(ticker):
        url = TIINGO_URL.format(ticker=variant.lower())
        params = {"startDate": start, "endDate": end, "format": "json"}
        headers = {"Authorization": f"Token {TIINGO_API_KEY}"}
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json()
            if not data:
                continue
            df = pd.DataFrame(data)
            df["Date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            df = df.set_index("Date")
            df = df.rename(
                columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
            )
            keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
            return df[keep]
        except Exception:
            continue
    return pd.DataFrame()


def _history_uncached(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Try yfinance, fall back to Tiingo when Yahoo rate-limits."""
    # 1. yfinance
    try:
        df = _fetch_yfinance(ticker, start, end)
        if df is not None and not df.empty:
            return df
        # empty result (delisted or bad ticker) — fall through to Tiingo
    except RateLimited:
        df = _fetch_tiingo(ticker, start, end)
        if not df.empty:
            return df
        raise  # no Tiingo data either; propagate rate limit to caller

    # yfinance returned empty; try Tiingo as secondary source
    alt = _fetch_tiingo(ticker, start, end)
    if not alt.empty:
        return alt
    return df if df is not None else pd.DataFrame()


def tiingo_enabled() -> bool:
    return bool(TIINGO_API_KEY)


@lru_cache(maxsize=256)
def _cached_history_tuple(ticker: str, start: str, end: str) -> tuple:
    """In-memory cache around the disk cache."""
    path = _cache_path(ticker, start, end)
    if path.exists():
        try:
            hist = pd.read_parquet(path)
        except Exception:
            hist = None
        if hist is not None:
            return _rows_from_frame(hist)

    hist = _history_uncached(ticker, start, end)
    if hist is not None and not hist.empty:
        try:
            hist.to_parquet(path)
        except Exception:
            pass
    return _rows_from_frame(hist)


def _rows_from_frame(hist: pd.DataFrame | None) -> tuple:
    if hist is None or hist.empty:
        return tuple()
    rows: list[tuple[str, float, float, float, float, int]] = []
    for idx, row in hist.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        rows.append(
            (
                d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
                float(row.get("Open", 0.0) or 0.0),
                float(row.get("High", 0.0) or 0.0),
                float(row.get("Low", 0.0) or 0.0),
                float(row.get("Close", 0.0) or 0.0),
                int(row.get("Volume", 0) or 0),
            )
        )
    return tuple(rows)


def get_history(ticker: str, start: str, end: str) -> list[dict[str, Any]]:
    """Return OHLCV rows for [start, end]. Raises RateLimited if Yahoo throttles us."""
    rows = _cached_history_tuple(ticker.upper(), start, end)
    return [
        {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
        for d, o, h, l, c, v in rows
    ]


def get_info(ticker: str, max_age_days: int = 7) -> dict[str, Any]:
    """Return sector/industry/market cap etc. Disk-cached with TTL."""
    path = _info_cache_path(ticker.upper())
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            ts = cached.get("_fetched_at", 0)
            if time.time() - ts < max_age_days * 86400:
                return {k: v for k, v in cached.items() if k != "_fetched_at"}
        except Exception:
            pass

    try:
        info = yf.Ticker(ticker.upper()).info or {}
    except Exception as e:
        if _is_rate_limit_error(e):
            # Don't raise from get_info — many callers can tolerate missing metadata
            return _empty_info()
        info = {}
    out = {
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "long_name": info.get("longName") or info.get("shortName") or "",
        "market_cap": info.get("marketCap"),
        "exchange": info.get("exchange") or "",
        "currency": info.get("currency") or "USD",
        "website": info.get("website") or "",
    }
    try:
        path.write_text(json.dumps({**out, "_fetched_at": time.time()}))
    except Exception:
        pass
    return out


def _empty_info() -> dict[str, Any]:
    return {
        "sector": "",
        "industry": "",
        "long_name": "",
        "market_cap": None,
        "exchange": "",
        "currency": "USD",
        "website": "",
    }

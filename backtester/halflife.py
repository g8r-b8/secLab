"""Signal half-life study.

For each signal in a preset's output, compute the cumulative forward return
from entry (T+1 close) up to entry + N days for N in 1..max_days. Aggregate
across all signals to produce a decay curve:

  day = 1, 2, 3, ..., N
  mean cum return = average across all signals of (price[entry+day] / price[entry] - 1)

This isolates the SIGNAL's raw predictive power from the strategy's sizing /
stop-loss / exit noise. Directly answers:

  - Peak day: when does the average signal reach its maximum edge?
  - Half-life: how many days until marginal return drops to half of its peak?
  - Is the signal front-loaded (first-day spike) or slow-build?

If peak day is 3 but we're holding 20 days, we're giving back alpha. If peak
day is 30 but we're holding 5, we're leaving money on the table.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

import pandas as pd

from backtester.core import Signal

log = logging.getLogger("seclab")


def compute_halflife(
    signals: list[Signal],
    prices: dict[str, "pd.DataFrame"],
    max_days: int = 60,
) -> dict[str, Any] | None:
    """Cross-sectional forward-return decay curve.

    Returns a dict with:
      - `curve`: [{day, mean_cum_return, median_cum_return, stderr, n, mean_daily}]
      - `peak_day`, `peak_cum_return`
      - `half_life_days`: N where curve[N].mean_cum_return ≈ peak_cum_return / 2
          computed as linear interpolation on the curve; None if never
          crosses half (e.g. monotonic decay or noise)
      - `n_signals_used`, `n_signals_dropped`
      - `front_loaded_pct`: fraction of peak earned in day 1
    """
    if not signals:
        return None

    # For each signal, grab the forward-price path starting at entry.
    # Entry = first trading day AT-OR-AFTER signal_date that has a bar.
    # We use that bar's Close as the baseline.
    returns_by_day: list[list[float]] = [[] for _ in range(max_days + 1)]
    n_used = 0
    n_dropped = 0

    for s in signals:
        df = prices.get(s.ticker.upper())
        if df is None or df.empty or "Close" not in df.columns:
            n_dropped += 1
            continue
        try:
            sig_dt = dt.date.fromisoformat(s.signal_date)
        except Exception:
            n_dropped += 1
            continue
        # Find the first index at or after signal_date
        idx_dates = df.index.date if hasattr(df.index, "date") else df.index.map(lambda d: d.date() if hasattr(d, "date") else d)
        future = df[idx_dates >= sig_dt]
        if len(future) < 2:
            n_dropped += 1
            continue
        # Use second bar as entry (T+1 close convention used throughout the
        # backtester — don't peek at signal-day close).
        closes = future["Close"].to_numpy()
        if len(closes) < 2:
            n_dropped += 1
            continue
        base = float(closes[1])
        if base <= 0:
            n_dropped += 1
            continue
        # Collect forward returns for available days, cap at max_days
        horizon = min(max_days, len(closes) - 2)
        direction = int(s.direction or 1)
        for d in range(1, horizon + 1):
            p = float(closes[1 + d])
            if p <= 0:
                continue
            r = direction * (p / base - 1.0)
            returns_by_day[d].append(r)
        n_used += 1

    if n_used < 10:
        return None

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _median(xs: list[float]) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        n = len(s)
        m = n // 2
        return s[m] if n % 2 else (s[m - 1] + s[m]) / 2

    def _stderr(xs: list[float], mu: float) -> float:
        if len(xs) < 2:
            return 0.0
        var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
        return math.sqrt(max(var, 0.0) / len(xs))

    curve: list[dict[str, Any]] = []
    prev_mean = 0.0
    for d in range(1, max_days + 1):
        xs = returns_by_day[d]
        if not xs:
            continue
        mu = _mean(xs)
        med = _median(xs)
        se = _stderr(xs, mu)
        # Marginal daily return = diff from previous day's mean cum return
        daily = mu - prev_mean
        curve.append({
            "day": d,
            "mean_cum_return": mu,
            "median_cum_return": med,
            "stderr": se,
            "n": len(xs),
            "mean_daily": daily,
        })
        prev_mean = mu

    if not curve:
        return None

    # Peak day = day of max mean_cum_return
    peak_row = max(curve, key=lambda r: r["mean_cum_return"])
    peak_day = int(peak_row["day"])
    peak_cum = float(peak_row["mean_cum_return"])

    # Half-life: first day N after peak where mean_cum_return <= peak/2.
    # Interpolate linearly between the bracketing days.
    half_target = peak_cum / 2.0
    half_life: float | None = None
    if peak_cum > 0:
        for i, row in enumerate(curve):
            if row["day"] <= peak_day:
                continue
            if row["mean_cum_return"] <= half_target:
                # interpolate between previous row and this one
                if i > 0:
                    prev = curve[i - 1]
                    num = prev["mean_cum_return"] - half_target
                    den = prev["mean_cum_return"] - row["mean_cum_return"]
                    frac = (num / den) if den > 0 else 0.0
                    half_life = float(prev["day"] + frac * (row["day"] - prev["day"]))
                else:
                    half_life = float(row["day"])
                break

    # Fraction of peak captured on day 1 (front-loading test)
    day1 = next((r for r in curve if r["day"] == 1), None)
    front_loaded_pct = (day1["mean_cum_return"] / peak_cum) if day1 and peak_cum > 0 else None

    return {
        "curve": curve,
        "peak_day": peak_day,
        "peak_cum_return": peak_cum,
        "half_life_days": half_life,
        "n_signals_used": n_used,
        "n_signals_dropped": n_dropped,
        "max_days": max_days,
        "front_loaded_pct": front_loaded_pct,
    }

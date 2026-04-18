"""Information Coefficient (IC) study — per-horizon Spearman rank
correlation between signal strength and forward returns.

Answers: does our `signal.strength` actually rank future-return performance
cross-sectionally? If IC is zero across all horizons, strength is a
decorative field with no predictive power.

IC interpretation (rule of thumb):
  IC > 0.05   meaningful signal
  IC > 0.10   strong
  IC < 0       signal is inverted — strong sells are returning MORE than strong buys
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

import pandas as pd

from backtester.core import Signal

log = logging.getLogger("seclab")


def _rank(xs: list[float]) -> list[float]:
    """Average-rank assignment (handles ties via mean of tied ranks)."""
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        # average rank for this tie group (1-indexed)
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    ra = _rank(a)
    rb = _rank(b)
    ma = sum(ra) / len(ra)
    mb = sum(rb) / len(rb)
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(len(a)))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def compute_ic(
    signals: list[Signal],
    prices: dict[str, pd.DataFrame],
    horizons: list[int] | None = None,
) -> dict[str, Any] | None:
    """Compute IC at multiple forward horizons.

    For each horizon h ∈ horizons, pair (strength, forward-return-h) across
    all signals and compute Spearman rank correlation.
    """
    if not signals:
        return None
    horizons = horizons or [1, 3, 5, 10, 20, 40, 60]

    # Per-signal, collect strength + the forward return at each horizon
    per_h: dict[int, list[tuple[float, float]]] = {h: [] for h in horizons}

    for s in signals:
        df = prices.get(s.ticker.upper())
        if df is None or df.empty or "Close" not in df.columns:
            continue
        try:
            sig_dt = dt.date.fromisoformat(s.signal_date)
        except Exception:
            continue
        idx_dates = (df.index.date if hasattr(df.index, "date")
                     else df.index.map(lambda d: d.date() if hasattr(d, "date") else d))
        future = df[idx_dates >= sig_dt]
        if len(future) < 2:
            continue
        closes = future["Close"].to_numpy()
        if len(closes) < 2:
            continue
        entry = float(closes[1])
        if entry <= 0:
            continue
        direction = int(s.direction or 1)
        strength = float(s.strength or 1.0)
        for h in horizons:
            if h + 1 < len(closes):
                fwd = float(closes[1 + h])
                if fwd > 0:
                    r = direction * (fwd / entry - 1.0)
                    per_h[h].append((strength, r))

    out_by_horizon: list[dict[str, Any]] = []
    for h in horizons:
        pairs = per_h[h]
        if len(pairs) < 30:
            out_by_horizon.append({"horizon": h, "n": len(pairs), "ic": None})
            continue
        strengths = [p[0] for p in pairs]
        fwds = [p[1] for p in pairs]
        ic = _spearman(strengths, fwds)
        # approximate t-stat for IC
        n = len(pairs)
        t = ic * math.sqrt(max(n - 2, 1)) / math.sqrt(max(1 - ic * ic, 1e-9))
        out_by_horizon.append({
            "horizon": h,
            "n": n,
            "ic": float(ic),
            "t_stat": float(t),
            "significant": abs(t) > 2.0,
        })
    # Pick the horizon with peak |IC| for the headline
    best = max(
        (r for r in out_by_horizon if r["ic"] is not None),
        key=lambda r: abs(r["ic"]),
        default=None,
    )
    return {
        "by_horizon": out_by_horizon,
        "best_horizon_days": best["horizon"] if best else None,
        "best_ic": best["ic"] if best else None,
        "best_t_stat": best.get("t_stat") if best else None,
    }

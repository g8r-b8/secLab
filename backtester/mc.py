"""Monte Carlo equity-path simulation.

Bootstrap-resamples historical daily returns (stationary or block bootstrap)
and simulates forward equity paths. Output is the distribution of terminal
equities and a percentile envelope for the equity curve.

Useful for answering 'what's the realistic range of outcomes over the next
N trading days?' without imposing a parametric distribution.
"""
from __future__ import annotations

import math
import random
from typing import Any


def simulate_paths(
    historical_returns: list[float],
    *,
    horizon_days: int = 252,
    n_paths: int = 1000,
    block_len: int = 5,
    starting_equity: float = 1.0,
    seed: int = 42,
) -> dict[str, Any]:
    """Block bootstrap N paths of length `horizon_days`.

    Block length > 1 preserves short-run autocorrelation (important for
    event-driven strategies with momentum in daily returns).

    Returns:
      paths_summary:      [{day, p5, p25, p50, p75, p95}] for the envelope chart
      terminal_stats:     {mean, p5, p50, p95, prob_positive}
      max_drawdown_stats: {p50, p95}
    """
    if len(historical_returns) < 30 or horizon_days <= 0:
        return {"insufficient_data": True}

    rng = random.Random(seed)
    hr = historical_returns
    n_hist = len(hr)
    block_len = max(1, min(block_len, n_hist))

    # For each of n_paths, build a sequence of `horizon_days` returns by
    # concatenating randomly-chosen blocks of length `block_len`.
    all_paths: list[list[float]] = []  # each: equity trajectory
    max_dds: list[float] = []
    for _ in range(n_paths):
        eq = starting_equity
        path = [eq]
        days_left = horizon_days
        peak = eq
        max_dd = 0.0
        while days_left > 0:
            start_idx = rng.randint(0, n_hist - 1)
            take = min(block_len, days_left)
            for j in range(take):
                r = hr[(start_idx + j) % n_hist]
                eq *= (1 + r)
                path.append(eq)
                peak = max(peak, eq)
                dd = (eq - peak) / peak if peak > 0 else 0.0
                if dd < max_dd:
                    max_dd = dd
            days_left -= take
        all_paths.append(path[: horizon_days + 1])
        max_dds.append(max_dd)

    # Build envelope by taking percentiles across paths at each day
    max_len = max(len(p) for p in all_paths)
    envelope: list[dict[str, float]] = []
    for t in range(max_len):
        col = [p[t] for p in all_paths if t < len(p)]
        col.sort()
        def pct(p: float) -> float:
            if not col:
                return 0.0
            k = (len(col) - 1) * p
            lo = int(math.floor(k))
            hi = int(math.ceil(k))
            if lo == hi:
                return col[lo]
            return col[lo] + (col[hi] - col[lo]) * (k - lo)
        envelope.append({
            "day": t,
            "p5": pct(0.05),
            "p25": pct(0.25),
            "p50": pct(0.50),
            "p75": pct(0.75),
            "p95": pct(0.95),
        })

    # Terminal stats
    term = sorted(p[-1] for p in all_paths)
    n = len(term)
    def tp(p: float) -> float:
        k = (n - 1) * p
        lo = int(math.floor(k))
        hi = int(math.ceil(k))
        if lo == hi:
            return term[lo]
        return term[lo] + (term[hi] - term[lo]) * (k - lo)
    term_mean = sum(term) / n if n else 0.0
    prob_positive = sum(1 for v in term if v > starting_equity) / max(1, n)

    dds = sorted(max_dds)
    m = len(dds)
    def dp(p: float) -> float:
        if not m:
            return 0.0
        k = (m - 1) * p
        lo = int(math.floor(k))
        hi = int(math.ceil(k))
        if lo == hi:
            return dds[lo]
        return dds[lo] + (dds[hi] - dds[lo]) * (k - lo)

    return {
        "horizon_days": horizon_days,
        "n_paths": n_paths,
        "block_len": block_len,
        "starting_equity": starting_equity,
        "envelope": envelope,
        "terminal": {
            "mean": term_mean,
            "p5": tp(0.05),
            "p50": tp(0.50),
            "p95": tp(0.95),
            "prob_positive": prob_positive,
        },
        "max_drawdown": {
            "p50": dp(0.50),
            "p95": dp(0.05),  # the 5th percentile of a signed drawdown = worst 5%
        },
    }

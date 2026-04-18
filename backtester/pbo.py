"""Probability of Backtest Overfitting (PBO) via Combinatorially-Symmetric
Cross-Validation (CSCV), per Bailey & López de Prado.

Given a matrix of per-combo daily returns (rows = days, cols = combos),
split rows into S groups, enumerate combinations of S/2 groups as the
"in-sample" half and the complementary half as "out-of-sample". For each
pair, pick the combo that maxes IS Sharpe, then check its OOS rank.

PBO = P(OOS rank of IS-best combo ≤ median). High PBO → backtests are
not diagnostic; our "best" looks best in one subset but underperforms
on the other half of the same data, suggesting overfitting.

Simpler implementation here uses S=16 groups and random sampling of the
(16 choose 8) = 12,870 combinations. 200 samples is plenty.
"""
from __future__ import annotations

import math
import random
from typing import Any


def _sharpe(rets: list[float]) -> float:
    n = len(rets)
    if n < 2:
        return 0.0
    m = sum(rets) / n
    v = sum((r - m) ** 2 for r in rets) / (n - 1)
    s = math.sqrt(max(v, 1e-12))
    return (m / s) * math.sqrt(252)


def compute_pbo(
    returns_matrix: list[list[float]],
    *,
    n_groups: int = 16,
    n_samples: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """
    returns_matrix: list of length = days, each item is a list of length
                    = n_combos. Matrix[t][c] = combo c's return on day t.
    """
    n_days = len(returns_matrix)
    if n_days < n_groups * 2:
        return {"insufficient_data": True, "n_days": n_days}
    n_combos = len(returns_matrix[0]) if n_days > 0 else 0
    if n_combos < 2:
        return {"insufficient_data": True, "n_combos": n_combos}

    group_size = n_days // n_groups
    groups: list[list[int]] = []
    for g in range(n_groups):
        groups.append(list(range(g * group_size, (g + 1) * group_size)))
    # any trailing rows go to the last group
    if group_size * n_groups < n_days:
        groups[-1].extend(range(group_size * n_groups, n_days))

    rng = random.Random(seed)
    half = n_groups // 2
    logits: list[float] = []

    for _ in range(n_samples):
        idxs = list(range(n_groups))
        rng.shuffle(idxs)
        is_groups = idxs[:half]
        oos_groups = idxs[half:]
        is_rows = [r for g in is_groups for r in groups[g]]
        oos_rows = [r for g in oos_groups for r in groups[g]]

        # Pick best combo by IS Sharpe
        is_sharpes: list[float] = []
        for c in range(n_combos):
            rets_is = [returns_matrix[t][c] for t in is_rows]
            is_sharpes.append(_sharpe(rets_is))
        best_is = max(range(n_combos), key=lambda c: is_sharpes[c])

        # Its OOS rank among all combos
        oos_sharpes: list[float] = []
        for c in range(n_combos):
            rets_oos = [returns_matrix[t][c] for t in oos_rows]
            oos_sharpes.append(_sharpe(rets_oos))
        # Rank of best_is in oos (1-indexed, 1 = best)
        oos_sorted = sorted(range(n_combos), key=lambda c: -oos_sharpes[c])
        rank = oos_sorted.index(best_is) + 1
        # omega = rank / (N+1)  ∈ (0, 1); logit(omega) used by original paper
        omega = rank / (n_combos + 1)
        omega = max(1e-6, min(1 - 1e-6, omega))
        logits.append(math.log(omega / (1 - omega)))

    # PBO = fraction of samples where IS-best had OOS rank WORSE than median
    n_worse = sum(1 for lg in logits if lg > 0)  # logit>0 means omega>0.5 ≡ below median
    pbo = n_worse / max(1, len(logits))
    # Mean logit — the paper's headline statistic
    mean_logit = sum(logits) / max(1, len(logits))
    return {
        "n_combos": n_combos,
        "n_days": n_days,
        "n_groups": n_groups,
        "n_samples": len(logits),
        "pbo": pbo,
        "mean_logit": mean_logit,
        "interpretation": (
            "Low overfitting — IS-best combos hold up out-of-sample"
            if pbo < 0.2
            else "Moderate overfitting risk" if pbo < 0.5
            else "High overfitting — backtest picks are likely data-mined"
        ),
    }

"""Performance metrics for backtest results."""
from __future__ import annotations

import math
import random
from typing import Any

import pandas as pd

from backtester.core import Trade

TRADING_DAYS = 252
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 42


def _sharpe(returns: list[float]) -> float:
    """Annualized Sharpe from a list of daily returns."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std <= 0:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS)


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _bootstrap_sharpe_ci(
    returns: list[float],
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, float]:
    """Resample daily returns with replacement, compute Sharpe on each, return percentiles."""
    if len(returns) < 10:
        return {"median": _sharpe(returns), "p5": 0.0, "p95": 0.0, "n_samples": 0}
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(returns)
    sharpes: list[float] = []
    for _ in range(samples):
        sample = [returns[rng.randrange(n)] for _ in range(n)]
        sharpes.append(_sharpe(sample))
    return {
        "median": _percentile(sharpes, 0.5),
        "p5": _percentile(sharpes, 0.05),
        "p95": _percentile(sharpes, 0.95),
        "n_samples": samples,
    }


def _bootstrap_mean_ci(
    xs: list[float],
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, float]:
    """Resample a list, return percentile-CI on the mean."""
    if len(xs) < 10:
        m = (sum(xs) / len(xs)) if xs else 0.0
        return {"median": m, "p5": m, "p95": m, "n_samples": 0}
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(xs)
    means: list[float] = []
    for _ in range(samples):
        means.append(sum(xs[rng.randrange(n)] for _ in range(n)) / n)
    return {
        "median": _percentile(means, 0.5),
        "p5": _percentile(means, 0.05),
        "p95": _percentile(means, 0.95),
        "n_samples": samples,
    }


def _rolling_sharpe(returns: list[float], window: int = 60) -> list[float | None]:
    """Rolling annualized Sharpe over `window` days. None where insufficient history."""
    out: list[float | None] = []
    for i in range(len(returns)):
        if i < window - 1:
            out.append(None)
            continue
        slice_ = returns[i - window + 1 : i + 1]
        out.append(_sharpe(slice_))
    return out


def _underwater(eq: list[float]) -> list[float]:
    """Drawdown at each point = equity / running-peak − 1 (≤ 0)."""
    peak = eq[0] if eq else 0
    out: list[float] = []
    for v in eq:
        peak = max(peak, v)
        out.append((v / peak - 1) if peak > 0 else 0.0)
    return out


def _value_at_risk(returns: list[float], confidence: float = 0.95) -> dict[str, float]:
    """Historical + parametric (Normal) + Cornish-Fisher VaR, plus ES (CVaR).

    Cornish-Fisher adjusts the Normal quantile by the sample's skewness and
    excess kurtosis — "parametric VaR under heavier tails" without fitting a
    full Student-t distribution. Matches practitioner convention: any daily-
    return series with fat left tails will show CF-VaR worse (more negative)
    than Normal parametric VaR.

    All VaRs are negative fractions: -0.028 means "95% VaR = -2.8%".
    """
    if not returns:
        return {
            "historical_var": 0.0, "parametric_var": 0.0, "cornish_fisher_var": 0.0,
            "cvar": 0.0, "skewness": 0.0, "excess_kurtosis": 0.0, "confidence": confidence,
        }
    alpha = 1 - confidence
    sorted_r = sorted(returns)
    # Historical VaR = alpha-percentile of the loss distribution
    hist_var = _percentile(sorted_r, alpha)
    # Expected Shortfall = mean of the worst alpha fraction
    cutoff_idx = max(1, int(len(sorted_r) * alpha))
    tail = sorted_r[:cutoff_idx]
    cvar = sum(tail) / len(tail) if tail else hist_var
    # Parametric VaR = -z × σ (z ≈ 1.645 for 95%, 2.326 for 99%)
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / max(1, n - 1)
    std = math.sqrt(var)
    # Inverse CDF approximation for common confidence levels
    z_table = {0.90: 1.282, 0.95: 1.645, 0.975: 1.96, 0.99: 2.326}
    z = z_table.get(round(confidence, 3), 1.645)
    parametric = mean - z * std

    # Cornish-Fisher: adjust z for higher moments. z points INTO the tail
    # (positive here), so the CF-adjusted quantile `z_cf` replaces z in
    # `mean - z × std`.
    skew = 0.0
    kurt = 0.0
    cf_var = parametric
    if n >= 30 and std > 0:
        skew = sum((r - mean) ** 3 for r in returns) / (n * std ** 3)
        kurt = sum((r - mean) ** 4 for r in returns) / (n * std ** 4) - 3.0
        z_cf = (
            z
            + (z ** 2 - 1) / 6.0 * skew
            + (z ** 3 - 3 * z) / 24.0 * kurt
            - (2 * z ** 3 - 5 * z) / 36.0 * (skew ** 2)
        )
        cf_var = mean - z_cf * std

    return {
        "historical_var": hist_var,
        "parametric_var": parametric,
        "cornish_fisher_var": cf_var,
        "cvar": cvar,
        "skewness": skew,
        "excess_kurtosis": kurt,
        "confidence": confidence,
    }


def _return_histogram(returns: list[float], n_bins: int = 40) -> dict[str, list]:
    """Bucketize returns into a histogram for the UI."""
    if not returns:
        return {"bins": [], "counts": []}
    lo = min(returns)
    hi = max(returns)
    if lo == hi:
        return {"bins": [lo], "counts": [len(returns)]}
    width = (hi - lo) / n_bins
    bins = [lo + i * width for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for r in returns:
        idx = min(int((r - lo) / width), n_bins - 1)
        counts[idx] += 1
    return {"bins": bins, "counts": counts}


def summarise(
    trades: list[Trade],
    equity_curve: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    starting_cash: float,
) -> dict[str, Any]:
    """Compute summary stats from trades + daily equity curve."""
    out: dict[str, Any] = {
        "n_trades": len(trades),
        "starting_cash": float(starting_cash),
    }

    if not trades:
        out["message"] = "No trades executed."
        return out

    # Trade-level stats
    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    out["n_wins"] = len(wins)
    out["n_losses"] = len(losses)
    out["win_rate"] = len(wins) / len(trades) if trades else 0.0
    out["avg_return_pct"] = sum(t.return_pct for t in trades) / len(trades)
    out["avg_win_pct"] = (sum(t.return_pct for t in wins) / len(wins)) if wins else 0.0
    out["avg_loss_pct"] = (sum(t.return_pct for t in losses) / len(losses)) if losses else 0.0
    out["median_return_pct"] = _median([t.return_pct for t in trades])
    out["best_pct"] = max(t.return_pct for t in trades)
    out["worst_pct"] = min(t.return_pct for t in trades)

    # Exit-reason breakdown
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    out["exits_by_reason"] = by_reason

    # Portfolio-level stats from equity curve
    if not equity_curve.empty:
        eq = equity_curve["equity"].astype(float).tolist()
        dates = [
            (d.isoformat() if hasattr(d, "isoformat") else str(d)[:10])
            for d in equity_curve["date"].tolist()
        ]
        final = eq[-1]
        out["final_equity"] = float(final)
        out["total_return_pct"] = (final / starting_cash) - 1
        days = len(eq)
        if days > 1 and final > 0:
            out["cagr"] = (final / starting_cash) ** (TRADING_DAYS / days) - 1
        else:
            out["cagr"] = 0.0

        # Daily returns
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        if rets:
            out["sharpe"] = _sharpe(rets)
            downside = [r for r in rets if r < 0]
            if downside:
                dvar = sum(r ** 2 for r in downside) / len(rets)
                dstd = math.sqrt(dvar)
                mean = sum(rets) / len(rets)
                out["sortino"] = (mean / dstd) * math.sqrt(TRADING_DAYS) if dstd > 0 else 0.0
            else:
                out["sortino"] = None

        # Bootstrap CIs on daily-returns Sharpe
        out["sharpe_ci"] = _bootstrap_sharpe_ci(rets)
        # Value-at-Risk and Expected Shortfall
        out["var_95"] = _value_at_risk(rets, confidence=0.95)
        out["var_99"] = _value_at_risk(rets, confidence=0.99)

        # Max drawdown + underwater curve + underwater DURATION
        # "Underwater" = below all-time running peak. Track the longest
        # continuous stretch and whether we end underwater.
        peak = eq[0]
        max_dd = 0.0
        max_dd_day_idx = 0
        longest_uw_days = 0
        current_uw_days = 0
        uw_start_idx: int | None = None
        longest_uw_range: tuple[int, int] = (0, 0)
        for i, v in enumerate(eq):
            peak = max(peak, v)
            dd = (v - peak) / peak if peak > 0 else 0
            if dd < max_dd:
                max_dd = dd
                max_dd_day_idx = i
            if dd < 0:
                if uw_start_idx is None:
                    uw_start_idx = i
                current_uw_days = i - uw_start_idx + 1
                if current_uw_days > longest_uw_days:
                    longest_uw_days = current_uw_days
                    longest_uw_range = (uw_start_idx, i)
            else:
                uw_start_idx = None
                current_uw_days = 0
        out["max_drawdown_pct"] = max_dd
        out["calmar"] = (out["cagr"] / abs(max_dd)) if max_dd < 0 else None
        out["max_drawdown_duration_days"] = longest_uw_days
        out["current_underwater_days"] = current_uw_days
        if longest_uw_days > 0:
            s_idx, e_idx = longest_uw_range
            out["max_drawdown_duration_range"] = {
                "start": dates[s_idx],
                "end": dates[e_idx],
            }

        # Monthly return heatmap — compound daily returns within each calendar
        # month. Rows = year, cols = month (1-12).
        monthly_map: dict[tuple[int, int], float] = {}
        for d, r in zip(dates[1:], rets):
            y = int(d[0:4])
            m = int(d[5:7])
            monthly_map[(y, m)] = (monthly_map.get((y, m), 1.0)) * (1 + r)
        monthly_rows: list[dict[str, Any]] = []
        years_seen = sorted({y for (y, _m) in monthly_map.keys()})
        for y in years_seen:
            row = {"year": y}
            cells = []
            for m in range(1, 13):
                if (y, m) in monthly_map:
                    cells.append({"month": m, "return_pct": monthly_map[(y, m)] - 1.0})
                else:
                    cells.append({"month": m, "return_pct": None})
            row["months"] = cells
            monthly_rows.append(row)
        out["monthly_returns"] = monthly_rows

        # Rolling 60-day Sharpe (paired with dates for the UI)
        r_sharpe = _rolling_sharpe(rets, window=60)
        # r_sharpe aligns with rets (len = days-1), pad front with None to match eq/dates
        padded = [None] + r_sharpe
        out["rolling_sharpe"] = [
            {"date": d, "sharpe": s}
            for d, s in zip(dates, padded)
        ]

        # Underwater curve (same length as eq)
        under = _underwater(eq)
        out["underwater"] = [
            {"date": d, "drawdown_pct": u}
            for d, u in zip(dates, under)
        ]

        # Daily return distribution
        out["return_distribution"] = _return_histogram(rets)
        out["daily_return_stats"] = {
            "mean": (sum(rets) / len(rets)) if rets else 0.0,
            "std": math.sqrt(sum((r - (sum(rets) / len(rets))) ** 2 for r in rets) / max(1, len(rets) - 1)) if rets else 0.0,
            "min": min(rets) if rets else 0.0,
            "max": max(rets) if rets else 0.0,
            "n_days": len(rets),
        }
    else:
        out["final_equity"] = float(starting_cash)
        out["total_return_pct"] = 0.0

    # Benchmark comparison
    if benchmark is not None and not benchmark.empty:
        bf = benchmark["equity"].astype(float).tolist()
        bench_final = bf[-1]
        out["benchmark"] = {
            "final_equity": float(bench_final),
            "total_return_pct": (bench_final / starting_cash) - 1,
        }
        if days > 1:
            b_rets = [bf[i] / bf[i - 1] - 1 for i in range(1, len(bf))]
            if b_rets:
                bmean = sum(b_rets) / len(b_rets)
                bvar = sum((r - bmean) ** 2 for r in b_rets) / max(1, len(b_rets) - 1)
                bstd = math.sqrt(bvar)
                out["benchmark"]["sharpe"] = (bmean / bstd) * math.sqrt(TRADING_DAYS) if bstd > 0 else 0.0
                # Information Ratio: annualized mean excess return over
                # tracking error (stdev of active returns). `rets` is the
                # strategy's daily returns, defined in the upstream block.
                n_align = min(len(rets), len(b_rets))
                if n_align >= 20:
                    active = [rets[i] - b_rets[i] for i in range(n_align)]
                    a_mean = sum(active) / n_align
                    a_var = sum((a - a_mean) ** 2 for a in active) / max(1, n_align - 1)
                    a_std = math.sqrt(a_var)
                    if a_std > 0:
                        out["information_ratio"] = (a_mean / a_std) * math.sqrt(TRADING_DAYS)
                        out["tracking_error_ann"] = a_std * math.sqrt(TRADING_DAYS)
        out["alpha_vs_benchmark_pct"] = out["total_return_pct"] - out["benchmark"]["total_return_pct"]

    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]

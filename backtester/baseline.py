"""Random-signal baseline — permutation test for backtest alpha.

For a preset over [start, end], compare the real signal's Sharpe to the
distribution of Sharpes from K randomized replications. Each replication
keeps the real signal's date distribution and ticker frequencies but shuffles
which ticker fires on which date. If the real Sharpe exceeds the random p95,
the (ticker, date) coupling carries genuine alpha — it's not just the
ticker-universe or the time-window talking.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from backtester.core import Backtest, Signal, _fetch_prices
from backtester.signals import PRESETS

log = logging.getLogger("seclab")

_WORKER: dict[str, Any] = {}


def _worker_init(template, prices):
    _WORKER["template"] = template
    _WORKER["prices"] = prices


def _permuted_signals(seed: int) -> list[Signal]:
    rng = random.Random(seed)
    template = _WORKER["template"]
    tickers = [t for t, _ in template]
    dates = [d for _, d in template]
    rng.shuffle(tickers)
    return [
        Signal(ticker=t, signal_date=d, direction=1, label="permuted", strength=1.0)
        for t, d in zip(tickers, dates)
    ]


def _worker_run_random(
    seed: int,
    start: str,
    end: str,
    hold: int,
    stop: float,
    target: float,
    position_mode: str,
    position_size: float,
    position_pct: float,
    slippage_bps: float,
    commission_per_trade: float,
) -> dict:
    bt = Backtest(
        signals=_permuted_signals(seed),
        start=start,
        end=end,
        hold_days=hold,
        stop_loss_pct=stop or None,
        profit_target_pct=target or None,
        position_mode=position_mode,
        position_size=position_size,
        position_pct=position_pct,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        benchmark=None,
        prefetched_prices=_WORKER["prices"],
    )
    result = bt.run()
    s = result.summary
    return {
        "seed": seed,
        "n_trades": int(s.get("n_trades", 0)),
        "sharpe": float(s.get("sharpe", 0.0) or 0.0),
        "total_return_pct": float(s.get("total_return_pct", 0.0) or 0.0),
        "max_drawdown_pct": float(s.get("max_drawdown_pct", 0.0) or 0.0),
    }


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run_random_baseline(
    preset: str,
    start: str,
    end: str,
    n_iterations: int = 20,
    hold_days: int = 20,
    stop_loss_pct: float = 0.0,
    profit_target_pct: float = 0.0,
    position_mode: str = "fixed_usd",
    position_size: float = 10_000.0,
    position_pct: float = 0.02,
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    benchmark: str = "SPY",
    seed: int = 42,
    workers: int | None = None,
) -> dict[str, Any]:
    """Run a permutation test: real signal vs K randomised (ticker-shuffled) replicas.

    The universe is the set of tickers the real preset fired on in [start, end].
    Each replication keeps the date-distribution and per-ticker frequency but
    scrambles the (ticker, date) pairings. A p-value = fraction of random
    replicas with Sharpe ≥ real Sharpe. Low p ⇒ real alpha.
    """
    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        return {"error": f"unknown preset {preset}", "available": list(PRESETS)}

    signals = preset_cfg["fn"](start=start, end=end)
    if not signals:
        return {"error": "preset produced zero signals"}

    template = [(s.ticker.upper(), s.signal_date) for s in signals]
    universe = sorted({t for t, _ in template})

    pad = 60
    sd = (dt.date.fromisoformat(start) - dt.timedelta(days=pad)).isoformat()
    ed = (dt.date.fromisoformat(end) + dt.timedelta(days=pad)).isoformat()
    tickers_to_fetch = set(universe)
    if benchmark:
        tickers_to_fetch.add(benchmark)
    log.info(
        f"[baseline] {preset} {start}->{end}: {len(signals)} signals, "
        f"{len(universe)} universe, {n_iterations} iterations"
    )
    prices = _fetch_prices(tickers_to_fetch, sd, ed)
    log.info(f"[baseline] prefetched {len(prices)}/{len(tickers_to_fetch)} tickers")

    real_bt = Backtest(
        signals=signals,
        start=start,
        end=end,
        hold_days=hold_days,
        stop_loss_pct=stop_loss_pct or None,
        profit_target_pct=profit_target_pct or None,
        position_mode=position_mode,
        position_size=position_size,
        position_pct=position_pct,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        benchmark=benchmark or None,
        prefetched_prices=prices,
    )
    real = real_bt.run().summary
    real_sharpe = float(real.get("sharpe", 0.0) or 0.0)

    w = workers if workers is not None else min(n_iterations, max(1, (os.cpu_count() or 4) - 1))
    rng = random.Random(seed)
    seeds = [rng.randrange(1_000_000_000) for _ in range(n_iterations)]

    randoms: list[dict] = []
    with ProcessPoolExecutor(
        max_workers=w,
        initializer=_worker_init,
        initargs=(template, prices),
    ) as ex:
        futs = [
            ex.submit(
                _worker_run_random,
                s,
                start,
                end,
                hold_days,
                stop_loss_pct,
                profit_target_pct,
                position_mode,
                position_size,
                position_pct,
                slippage_bps,
                commission_per_trade,
            )
            for s in seeds
        ]
        for fut in as_completed(futs):
            randoms.append(fut.result())

    rand_sharpes = [r["sharpe"] for r in randoms]
    n_at_or_above = sum(1 for s in rand_sharpes if s >= real_sharpe)
    p_value = (n_at_or_above + 1) / (len(rand_sharpes) + 1)

    return {
        "preset": preset,
        "label": preset_cfg["label"],
        "start": start,
        "end": end,
        "n_signals": len(signals),
        "universe_size": len(universe),
        "params": {
            "hold_days": hold_days,
            "stop_loss_pct": stop_loss_pct,
            "profit_target_pct": profit_target_pct,
            "position_mode": position_mode,
            "position_size": position_size,
            "position_pct": position_pct,
            "slippage_bps": slippage_bps,
            "commission_per_trade": commission_per_trade,
        },
        "real": {
            "sharpe": real_sharpe,
            "total_return_pct": float(real.get("total_return_pct", 0.0) or 0.0),
            "max_drawdown_pct": float(real.get("max_drawdown_pct", 0.0) or 0.0),
            "n_trades": int(real.get("n_trades", 0)),
        },
        "random": {
            "n_iterations": len(randoms),
            "median": _pct(rand_sharpes, 0.5),
            "p5": _pct(rand_sharpes, 0.05),
            "p95": _pct(rand_sharpes, 0.95),
            "mean": (sum(rand_sharpes) / len(rand_sharpes)) if rand_sharpes else 0.0,
            "iterations": sorted(randoms, key=lambda r: r["sharpe"]),
        },
        "p_value": p_value,
        "interpretation": (
            "Real signal outperforms random permutations (p<0.05)"
            if p_value < 0.05
            else "Real signal is not statistically distinguishable from random permutations at 95%"
        ),
    }

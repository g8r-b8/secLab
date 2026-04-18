"""Walk-forward validation — does a sweep's best config survive on held-out data?

Flow:
  1. Split [start, end] into N equal folds.
  2. For each adjacent (train, test) fold pair:
       a. Run the full parameter sweep on train.
       b. Take the top_n configs by `rank_by` (Sharpe or alpha).
       c. Re-run each of those fixed configs on test.
  3. Return train-vs-test metrics side-by-side per config per iteration.

If the configs that won the train fold still beat SPY on the test fold, the
signal has genuine alpha. If they collapse, the sweep was overfitting.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from backtester.core import Backtest, _fetch_prices
from backtester.signals import PRESETS
from backtester.sweep import _run_single_preset_sweep

log = logging.getLogger("seclab")


def _split_window(start: str, end: str, n_folds: int) -> list[tuple[str, str]]:
    sd = dt.date.fromisoformat(start)
    ed = dt.date.fromisoformat(end)
    total_days = (ed - sd).days
    if total_days <= 0 or n_folds < 2:
        return [(start, end)]
    fold_size = total_days // n_folds
    folds: list[tuple[str, str]] = []
    for i in range(n_folds):
        fs = sd + dt.timedelta(days=i * fold_size)
        fe = sd + dt.timedelta(days=(i + 1) * fold_size) if i < n_folds - 1 else ed
        folds.append((fs.isoformat(), fe.isoformat()))
    return folds


def _backtest_one(
    preset: str,
    label: str,
    start: str,
    end: str,
    hold_days: int,
    stop_loss_pct: float | None,
    profit_target_pct: float | None,
    trailing_stop_pct: float | None,
    position_size: float,
    benchmark: str,
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    one_position_per_ticker: bool = False,
    position_mode: str = "fixed_usd",
    position_pct: float = 0.02,
) -> dict:
    signals = PRESETS[preset]["fn"](start=start, end=end)
    if not signals:
        return {
            "preset": preset,
            "label": label,
            "n_trades": 0,
            "message": "0 signals in this fold",
        }
    sd = dt.date.fromisoformat(start) - dt.timedelta(days=60)
    ed = dt.date.fromisoformat(end) + dt.timedelta(days=60)
    tickers = {s.ticker for s in signals} | ({benchmark} if benchmark else set())
    prices = _fetch_prices(tickers, sd.isoformat(), ed.isoformat())
    bt = Backtest(
        signals=signals,
        start=start, end=end,
        hold_days=hold_days,
        stop_loss_pct=stop_loss_pct or None,
        profit_target_pct=profit_target_pct or None,
        trailing_stop_pct=trailing_stop_pct or None,
        position_mode=position_mode if position_mode in ("fixed_usd", "pct_portfolio") else "fixed_usd",
        position_size=position_size,
        position_pct=position_pct,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        one_position_per_ticker=one_position_per_ticker,
        benchmark=benchmark or None,
        prefetched_prices=prices,
    )
    result = bt.run()
    s = result.summary
    return {
        "preset": preset,
        "label": label,
        "n_signals": len(signals),
        "n_trades": s.get("n_trades"),
        "win_rate": s.get("win_rate"),
        "total_return_pct": s.get("total_return_pct"),
        "cagr": s.get("cagr"),
        "sharpe": s.get("sharpe"),
        "sortino": s.get("sortino"),
        "max_drawdown_pct": s.get("max_drawdown_pct"),
        "benchmark_return_pct": (s.get("benchmark") or {}).get("total_return_pct"),
        "alpha_vs_benchmark_pct": s.get("alpha_vs_benchmark_pct"),
    }


def run_walkforward(
    preset: str,
    start: str,
    end: str,
    hold_days: Iterable[int],
    stop_loss_pct: Iterable[float],
    profit_target_pct: Iterable[float],
    trailing_stop_pct: Iterable[float] | None = None,
    n_folds: int = 2,
    top_n: int = 5,
    rank_by: str = "sharpe",
    position_size: float = 10_000,
    benchmark: str = "SPY",
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    one_position_per_ticker: bool = False,
    position_mode: str = "fixed_usd",
    position_pct: float = 0.02,
) -> dict:
    """For each adjacent (train, test) fold, sweep train then evaluate top-N on test."""
    if rank_by not in ("sharpe", "alpha_vs_benchmark_pct", "cagr", "total_return_pct"):
        rank_by = "sharpe"
    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        return {"error": f"unknown preset {preset}"}

    hold_list = sorted(set(int(h) for h in hold_days))
    stop_list = sorted(set(float(s) for s in stop_loss_pct))
    target_list = sorted(set(float(t) for t in profit_target_pct))
    trail_list = sorted(set(float(t) for t in (trailing_stop_pct or [0.0])))

    folds = _split_window(start, end, max(2, n_folds))
    log.info(f"walkforward: {preset}  {len(folds)} folds  grid={len(hold_list)*len(stop_list)*len(target_list)*len(trail_list)}")

    iterations: list[dict] = []
    for i in range(len(folds) - 1):
        train_start, train_end = folds[i]
        test_start, test_end = folds[i + 1]
        log.info(f"  walkforward iter {i+1}: train {train_start}→{train_end}  test {test_start}→{test_end}")

        # 1. Sweep on train
        train_result = _run_single_preset_sweep(
            preset=preset, start=train_start, end=train_end,
            hold_list=hold_list, stop_list=stop_list,
            target_list=target_list, trail_list=trail_list,
            position_size=position_size, benchmark=benchmark,
            price_pad_days=60,
            slippage_bps=slippage_bps,
            commission_per_trade=commission_per_trade,
            one_position_per_ticker=one_position_per_ticker,
            position_mode=position_mode,
            position_pct=position_pct,
        )
        train_combos = train_result.get("combos", [])
        if not train_combos:
            iterations.append({
                "train_period": {"start": train_start, "end": train_end},
                "test_period": {"start": test_start, "end": test_end},
                "message": "no combos in train",
                "results": [],
            })
            continue

        top_train = sorted(train_combos, key=lambda c: -(c.get(rank_by) or -1e9))[:top_n]

        # 2. Evaluate each top config on test fold (fresh Backtest per config)
        results = []
        for cfg in top_train:
            test_metrics = _backtest_one(
                preset=preset,
                label=preset_cfg["label"],
                start=test_start,
                end=test_end,
                hold_days=cfg["hold_days"],
                stop_loss_pct=cfg["stop_loss_pct"] or None,
                profit_target_pct=cfg["profit_target_pct"] or None,
                trailing_stop_pct=cfg["trailing_stop_pct"] or None,
                position_size=position_size,
                benchmark=benchmark,
                slippage_bps=slippage_bps,
                commission_per_trade=commission_per_trade,
                one_position_per_ticker=one_position_per_ticker,
                position_mode=position_mode,
                position_pct=position_pct,
            )
            results.append({
                "params": {
                    "hold_days": cfg["hold_days"],
                    "stop_loss_pct": cfg["stop_loss_pct"],
                    "profit_target_pct": cfg["profit_target_pct"],
                    "trailing_stop_pct": cfg["trailing_stop_pct"],
                },
                "train": {
                    "sharpe": cfg.get("sharpe"),
                    "cagr": cfg.get("cagr"),
                    "total_return_pct": cfg.get("total_return_pct"),
                    "max_drawdown_pct": cfg.get("max_drawdown_pct"),
                    "alpha_vs_benchmark_pct": cfg.get("alpha_vs_benchmark_pct"),
                    "n_trades": cfg.get("n_trades"),
                },
                "test": {
                    "sharpe": test_metrics.get("sharpe"),
                    "cagr": test_metrics.get("cagr"),
                    "total_return_pct": test_metrics.get("total_return_pct"),
                    "max_drawdown_pct": test_metrics.get("max_drawdown_pct"),
                    "alpha_vs_benchmark_pct": test_metrics.get("alpha_vs_benchmark_pct"),
                    "n_trades": test_metrics.get("n_trades"),
                    "message": test_metrics.get("message"),
                },
            })

        iterations.append({
            "train_period": {"start": train_start, "end": train_end, "n_signals": train_result.get("signals", 0)},
            "test_period": {"start": test_start, "end": test_end},
            "results": results,
        })

    # Summary: how many top-picks held up on test (positive alpha out of sample)?
    total_configs = sum(len(i.get("results", [])) for i in iterations)
    held_up = sum(
        1 for i in iterations
        for r in i.get("results", [])
        if (r["test"].get("alpha_vs_benchmark_pct") or 0) > 0
    )

    return {
        "preset": preset,
        "label": preset_cfg["label"],
        "range": {"start": start, "end": end},
        "n_folds": len(folds),
        "rank_by": rank_by,
        "top_n": top_n,
        "summary": {
            "total_configs_tested": total_configs,
            "held_up_out_of_sample": held_up,
            "hold_rate": (held_up / total_configs) if total_configs else 0.0,
        },
        "iterations": iterations,
    }

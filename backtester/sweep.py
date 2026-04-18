"""Grid-search a backtest preset over (hold_days × stop_loss × profit_target).

Runs one price fetch per preset and reuses it across every iteration. The
(hold × stop × target) grid fans out across CPU cores via a ProcessPool —
each combo is independent, so N cores → ~N× speedup.
"""
from __future__ import annotations

import datetime as dt
import itertools
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Iterable

from backtester.core import Backtest, _fetch_prices
from backtester.signals import PRESETS

log = logging.getLogger("seclab")

# Workers stash the per-preset signals + prices in module-level state so we
# only pickle them once per pool (not per-task).
_WORKER: dict[str, Any] = {}


def _worker_init(signals, prices):
    _WORKER["signals"] = signals
    _WORKER["prices"] = prices


_ALLOWED_MODES = (
    "fixed_usd", "pct_portfolio", "risk_per_trade", "vol_target",
    "strength_weighted", "target_deployment",
)


def _worker_run(
    start: str,
    end: str,
    preset: str,
    label: str,
    hold: int,
    stop: float,
    target: float,
    trail: float,
    position_size: float,
    benchmark: str,
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    one_position_per_ticker: bool = False,
    position_mode: str = "fixed_usd",
    position_pct: float = 0.02,
    target_deployment_pct: float = 0.80,
    strength_scale_cap: float = 3.0,
) -> dict:
    bt = Backtest(
        signals=_WORKER["signals"],
        start=start,
        end=end,
        hold_days=hold,
        stop_loss_pct=stop or None,
        profit_target_pct=target or None,
        trailing_stop_pct=trail or None,
        position_mode=position_mode if position_mode in _ALLOWED_MODES else "fixed_usd",
        position_size=position_size,
        position_pct=position_pct,
        target_deployment_pct=target_deployment_pct,
        strength_scale_cap=strength_scale_cap,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        one_position_per_ticker=one_position_per_ticker,
        benchmark=benchmark or None,
        prefetched_prices=_WORKER["prices"],
    )
    result = bt.run()
    s = result.summary
    return {
        "preset": preset,
        "label": label,
        "hold_days": hold,
        "stop_loss_pct": stop,
        "profit_target_pct": target,
        "trailing_stop_pct": trail,
        "n_trades": s.get("n_trades"),
        "win_rate": s.get("win_rate"),
        "total_return_pct": s.get("total_return_pct"),
        "cagr": s.get("cagr"),
        "sharpe": s.get("sharpe"),
        "sortino": s.get("sortino"),
        "max_drawdown_pct": s.get("max_drawdown_pct"),
        "avg_return_pct": s.get("avg_return_pct"),
        "benchmark_return_pct": (s.get("benchmark") or {}).get("total_return_pct"),
        "alpha_vs_benchmark_pct": s.get("alpha_vs_benchmark_pct"),
        "exits_by_reason": s.get("exits_by_reason"),
    }


def _run_single_preset_sweep(
    preset: str,
    start: str,
    end: str,
    hold_list: list[int],
    stop_list: list[float],
    target_list: list[float],
    trail_list: list[float],
    position_size: float,
    benchmark: str,
    price_pad_days: int,
    workers: int | None = None,
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    one_position_per_ticker: bool = False,
    position_mode: str = "fixed_usd",
    position_pct: float = 0.02,
    target_deployment_pct: float = 0.80,
    strength_scale_cap: float = 3.0,
) -> dict:
    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        return {"preset": preset, "label": preset, "error": f"unknown preset {preset}", "combos": []}

    signals = preset_cfg["fn"](start=start, end=end)
    if not signals:
        return {
            "preset": preset,
            "label": preset_cfg["label"],
            "signals": 0,
            "combos": [],
            "error": "Signal generator returned 0 hits for this window.",
        }

    sd = dt.date.fromisoformat(start) - dt.timedelta(days=price_pad_days)
    ed = dt.date.fromisoformat(end) + dt.timedelta(days=price_pad_days)
    tickers = {s.ticker for s in signals} | ({benchmark} if benchmark else set())
    log.info(f"  sweep {preset}: fetching prices for {len(tickers)} tickers")
    prices = _fetch_prices(tickers, sd.isoformat(), ed.isoformat())

    grid = list(itertools.product(hold_list, stop_list, target_list, trail_list))
    log.info(f"  sweep {preset}: {len(signals)} signals × {len(grid)} combos")

    # Fan combos across processes. Skip pool overhead for tiny grids.
    if workers is None:
        workers = max(1, min(len(grid), (os.cpu_count() or 4) - 1))
    combos: list[dict] = []
    if workers <= 1 or len(grid) <= 1:
        _worker_init(signals, prices)
        for hold, stop, target, trail in grid:
            combos.append(
                _worker_run(
                    start, end, preset, preset_cfg["label"],
                    hold, stop, target, trail, position_size, benchmark,
                    slippage_bps=slippage_bps,
                    commission_per_trade=commission_per_trade,
                    one_position_per_ticker=one_position_per_ticker,
                    position_mode=position_mode,
                    position_pct=position_pct,
                    target_deployment_pct=target_deployment_pct,
                    strength_scale_cap=strength_scale_cap,
                )
            )
    else:
        log.info(f"  sweep {preset}: fanning out to {workers} workers")
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(signals, prices),
        ) as pool:
            futures = [
                pool.submit(
                    _worker_run,
                    start, end, preset, preset_cfg["label"],
                    hold, stop, target, trail, position_size, benchmark,
                    slippage_bps, commission_per_trade, one_position_per_ticker,
                    position_mode, position_pct,
                    target_deployment_pct, strength_scale_cap,
                )
                for hold, stop, target, trail in grid
            ]
            for i, f in enumerate(as_completed(futures), 1):
                combos.append(f.result())
                if i % 8 == 0 or i == len(futures):
                    log.info(f"  sweep {preset}: {i}/{len(futures)} combos complete")

    return {
        "preset": preset,
        "label": preset_cfg["label"],
        "signals": len(signals),
        "combos": combos,
    }


def run_sweep(
    preset: str | list[str],
    start: str,
    end: str,
    hold_days: Iterable[int],
    stop_loss_pct: Iterable[float],
    profit_target_pct: Iterable[float],
    trailing_stop_pct: Iterable[float] | None = None,
    position_size: float = 10_000,
    benchmark: str = "SPY",
    price_pad_days: int = 60,
    slippage_bps: float = 0.0,
    commission_per_trade: float = 0.0,
    one_position_per_ticker: bool = False,
    position_mode: str = "fixed_usd",
    position_pct: float = 0.02,
    target_deployment_pct: float = 0.80,
    strength_scale_cap: float = 3.0,
) -> dict:
    """Sweep one or many presets over the same (hold × stop × target × trail) grid.

    Accepts `preset` as a single string or a comma-separated/list. Each preset
    is swept independently (prices fetched per preset's universe) and results
    are merged so the UI can rank combos across all signals.
    """
    if isinstance(preset, str):
        presets = [p.strip() for p in preset.split(",") if p.strip()]
    else:
        presets = [p for p in preset if p]
    if not presets:
        return {"error": "no presets specified", "combos": []}

    hold_list = sorted(set(int(h) for h in hold_days))
    stop_list = sorted(set(float(s) for s in stop_loss_pct))
    target_list = sorted(set(float(t) for t in profit_target_pct))
    trail_list = sorted(set(float(t) for t in (trailing_stop_pct or [0.0])))

    per_preset: list[dict] = []
    all_combos: list[dict] = []
    for p in presets:
        result = _run_single_preset_sweep(
            preset=p,
            start=start,
            end=end,
            hold_list=hold_list,
            stop_list=stop_list,
            target_list=target_list,
            trail_list=trail_list,
            position_size=position_size,
            benchmark=benchmark,
            price_pad_days=price_pad_days,
            slippage_bps=slippage_bps,
            commission_per_trade=commission_per_trade,
            one_position_per_ticker=one_position_per_ticker,
            position_mode=position_mode,
            position_pct=position_pct,
            target_deployment_pct=target_deployment_pct,
            strength_scale_cap=strength_scale_cap,
        )
        per_preset.append(
            {
                "preset": result.get("preset"),
                "label": result.get("label"),
                "signals": result.get("signals", 0),
                "combos_count": len(result.get("combos", [])),
                "error": result.get("error"),
            }
        )
        all_combos.extend(result.get("combos", []))

    return {
        "presets": per_preset,
        "range": {"start": start, "end": end},
        "grid_size_per_preset": len(hold_list) * len(stop_list) * len(target_list) * len(trail_list),
        "total_combos": len(all_combos),
        "combos": all_combos,
        "top_by_sharpe": sorted(all_combos, key=lambda c: -(c["sharpe"] or -1e9))[:10],
        "top_by_alpha": sorted(all_combos, key=lambda c: -(c["alpha_vs_benchmark_pct"] or -1e9))[:10],
    }

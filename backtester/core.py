"""Daily-bar event-driven backtester.

Conventions:
- Signals specify (ticker, signal_date, direction). Entries fill at the CLOSE of
  the next trading day (T+1 close) to avoid look-ahead bias.
- Fixed N-day hold exit by default; pluggable exit rule later.
- Equal-$ position sizing per signal. Portfolio is daily-MTM'd on every
  trading day an open position exists.
- Prices from yfinance, raw (not split-adjusted) to match Form 4 price context.

Output: equity curve (DataFrame date→equity), trade list, summary metrics.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd
import yfinance as yf


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Signal:
    ticker: str
    signal_date: str   # ISO YYYY-MM-DD — the day the signal was observed
    direction: int = 1  # +1 long, -1 short
    label: str = ""    # optional freeform identifier (e.g. insider name)
    strength: float = 1.0  # arbitrary signal-quality score; generators set higher = stronger


@dataclass
class Trade:
    ticker: str
    signal_date: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str  # 'time', 'stop', 'target', 'trailing'
    direction: int
    shares: float
    position_value: float  # notional at entry
    pnl: float
    return_pct: float
    label: str = ""
    strength: float = 1.0  # carries Signal.strength so sizing can weight by it


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.DataFrame        # columns: date, equity, cash, invested
    benchmark_curve: pd.DataFrame | None  # date, equity (same starting capital)
    summary: dict                     # metrics (see metrics.py)

    def to_json(self) -> dict:
        def _row_date(d) -> str:
            if isinstance(d, (dt.date, dt.datetime)):
                return d.isoformat()[:10]
            return str(d)[:10]

        curve = [
            {"date": _row_date(r.date), "equity": float(r.equity), "cash": float(r.cash), "invested": float(r.invested)}
            for r in self.equity_curve.itertuples()
        ]
        bench = None
        if self.benchmark_curve is not None and not self.benchmark_curve.empty:
            bench = [
                {"date": _row_date(r.date), "equity": float(r.equity)}
                for r in self.benchmark_curve.itertuples()
            ]
        return {
            "summary": self.summary,
            "equity_curve": curve,
            "benchmark_curve": bench,
            "trades": [
                {
                    "ticker": t.ticker,
                    "signal_date": t.signal_date,
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "direction": t.direction,
                    "shares": t.shares,
                    "position_value": t.position_value,
                    "pnl": t.pnl,
                    "return_pct": t.return_pct,
                    "label": t.label,
                    "strength": float(getattr(t, "strength", 1.0)),
                }
                for t in self.trades
            ],
        }


# ─── Price fetching (batched) ───────────────────────────────────────────────

_PRICE_COLS = ("Open", "High", "Low", "Close")


def _fetch_prices(
    tickers: Iterable[str], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Return {ticker: DataFrame(index=Date, cols=Open/High/Low/Close)}.

    Disk-cached at `.cache/prices/{ticker}_{start}_{end}.parquet` (shared with
    `providers/prices`). First call hits yfinance in 200-ticker batches; repeat
    calls for the same window load from parquet and never re-hit Yahoo.
    Failures (delisted / rate-limited) get negative-cached for 24 h so we
    don't keep retrying them.
    """
    import logging
    import time
    from pathlib import Path as _Path

    import json as _json

    log = logging.getLogger("seclab")
    unique = sorted({t.upper() for t in tickers if t})
    if not unique:
        return {}

    cache_dir = _Path(__file__).parent.parent / ".cache" / "prices"
    cache_dir.mkdir(parents=True, exist_ok=True)
    neg_cache_path = cache_dir / "_negative.json"
    now = time.time()
    try:
        neg_cache = _json.loads(neg_cache_path.read_text()) if neg_cache_path.exists() else {}
    except Exception:
        neg_cache = {}
    neg_cache = {
        k: v for k, v in neg_cache.items()
        if now - float(v.get("at", 0)) < 86400  # 24 h TTL
    }

    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for t in unique:
        path = cache_dir / f"{t}_{start}_{end}.parquet"
        if path.exists():
            try:
                out[t] = pd.read_parquet(path)
                continue
            except Exception:
                pass
        if t in neg_cache and neg_cache[t].get("range") == f"{start}|{end}":
            continue  # recently-failed, skip
        missing.append(t)

    if not missing:
        return out

    log.info(f"  _fetch_prices: {len(out)} cached, fetching {len(missing)} missing in batches")
    chunk = 200
    newly_neg: list[str] = []
    for i in range(0, len(missing), chunk):
        batch = missing[i : i + chunk]
        try:
            df = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=True,
                group_by="ticker",
            )
        except Exception as e:
            log.warning(f"  _fetch_prices: batch {i}-{i+len(batch)} failed: {e}")
            newly_neg.extend(batch)
            continue
        if df is None or df.empty:
            newly_neg.extend(batch)
            continue
        if len(batch) == 1:
            t = batch[0]
            cols = [c for c in _PRICE_COLS if c in df.columns]
            if "Close" in cols:
                tdf = df[cols].dropna(subset=["Close"])
                if len(tdf):
                    out[t] = tdf
                    try:
                        tdf.to_parquet(cache_dir / f"{t}_{start}_{end}.parquet")
                    except Exception:
                        pass
                    continue
            newly_neg.append(t)
            continue
        for t in batch:
            sub = {}
            for c in _PRICE_COLS:
                if (t, c) in df.columns:
                    sub[c] = df[(t, c)]
            if not sub or "Close" not in sub:
                newly_neg.append(t)
                continue
            tdf = pd.DataFrame(sub).dropna(subset=["Close"])
            if len(tdf):
                out[t] = tdf
                try:
                    tdf.to_parquet(cache_dir / f"{t}_{start}_{end}.parquet")
                except Exception:
                    pass
            else:
                newly_neg.append(t)

    # Tiingo fallback for tickers yfinance couldn't resolve — primarily the
    # survivorship-bias fix: delisted / acquired tickers that Yahoo silently
    # drops. Requires TIINGO_API_KEY in env. Free tier: 500 req/hr, 2000/day.
    if newly_neg:
        try:
            from providers.prices import _fetch_tiingo, tiingo_enabled
        except Exception:
            tiingo_enabled = lambda: False  # noqa: E731
            _fetch_tiingo = None  # type: ignore

        if tiingo_enabled() and _fetch_tiingo is not None:
            log.info(f"  _fetch_prices: trying Tiingo for {len(newly_neg)} delisted/missing tickers")
            recovered: list[str] = []
            still_missing: list[str] = []
            for t in newly_neg:
                try:
                    tdf = _fetch_tiingo(t, start, end)
                except Exception:
                    tdf = None
                if tdf is None or tdf.empty or "Close" not in tdf.columns:
                    still_missing.append(t)
                    continue
                keep = [c for c in _PRICE_COLS if c in tdf.columns]
                sub = tdf[keep].dropna(subset=["Close"])
                if sub.empty:
                    still_missing.append(t)
                    continue
                out[t] = sub
                try:
                    sub.to_parquet(cache_dir / f"{t}_{start}_{end}.parquet")
                except Exception:
                    pass
                recovered.append(t)
            log.info(f"  _fetch_prices: Tiingo recovered {len(recovered)}/{len(newly_neg)}")
            newly_neg = still_missing

    if newly_neg:
        for t in newly_neg:
            neg_cache[t] = {"at": now, "range": f"{start}|{end}"}
        try:
            neg_cache_path.write_text(_json.dumps(neg_cache))
        except Exception:
            pass
    return out


# ─── Core engine ────────────────────────────────────────────────────────────

@dataclass
class Backtest:
    signals: list[Signal]
    start: str                # window start for benchmark + equity curve
    end: str                  # window end (exclusive)
    hold_days: int = 20       # max trading days to hold. Also the time-exit trigger.
    # Position sizing — pick one mode.
    position_mode: str = "fixed_usd"  # 'fixed_usd', 'pct_portfolio', 'risk_per_trade', 'vol_target', 'strength_weighted', 'target_deployment', 'kelly'
    position_size: float = 10_000.0   # $ notional per signal (fixed_usd mode)
    position_pct: float = 0.02        # fraction of current equity (pct_portfolio mode; also per-position cap in strength_weighted + target_deployment)
    risk_per_trade_pct: float = 0.01  # $ risked per trade as fraction of equity (risk_per_trade mode; needs stop_loss_pct > 0)
    target_vol_pct: float = 0.10      # per-position annualised volatility target (vol_target mode)
    target_deployment_pct: float = 0.80  # target fraction of equity invested at any time (target_deployment mode)
    strength_scale_cap: float = 3.0   # clamp strength multiplier to [1/cap, cap] so extreme signals don't dominate (strength_weighted mode)
    kelly_fraction: float = 0.25      # fractional Kelly multiplier (kelly mode). 1.0 = full Kelly (aggressive), 0.25 = quarter-Kelly (standard)
    # Portfolio-level risk controls
    max_signals_per_day: int | None = None   # cap concurrent bets on any given signal date (ranked by strength)
    max_sector_pct: float | None = None      # skip new position if it pushes sector exposure over this fraction
    sector_lookup: dict | None = None        # ticker → sector string (populated from yfinance.info)
    # Correlation-aware sizing — shrink new position size when it's correlated
    # with open positions. Stacks with any position_mode.
    correlation_adjust: bool = False
    correlation_shrinkage: float = 0.5       # 0 = off, 1 = full shrinkage on avg_corr = 1
    correlation_lookback: int = 60           # days for the rolling correlation window
    # Exit rules (all optional, earliest trigger wins). Set to None/0 to disable.
    stop_loss_pct: float | None = None       # e.g. 0.10 = exit if -10% from entry (intraday)
    profit_target_pct: float | None = None   # e.g. 0.20 = exit if +20% from entry
    trailing_stop_pct: float | None = None   # e.g. 0.08 = exit if drops 8% from max favourable
    # Execution realism
    slippage_bps: float = 0.0          # e.g. 10 bps = 0.10% applied at both entry and exit
    commission_per_trade: float = 0.0  # flat $ per fill; deducted 2× (entry + exit)
    one_position_per_ticker: bool = False  # suppress new signals on a ticker while one is open
    starting_cash: float | None = None
    benchmark: str | None = "SPY"
    price_pad_days: int = 60
    # Optional pre-fetched price cache — sweeps pass one fetch's worth of data
    # across many iterations to avoid re-hitting yfinance per parameter combo.
    prefetched_prices: dict[str, "pd.DataFrame"] | None = None

    def run(self) -> BacktestResult:
        if not self.signals:
            return BacktestResult(
                trades=[],
                equity_curve=pd.DataFrame(columns=["date", "equity", "cash", "invested"]),
                benchmark_curve=None,
                summary={"n_trades": 0, "message": "No signals provided."},
            )

        # Window for price fetch (add padding on both sides)
        sd = dt.date.fromisoformat(self.start) - dt.timedelta(days=self.price_pad_days)
        ed = dt.date.fromisoformat(self.end) + dt.timedelta(days=self.price_pad_days)
        tickers = {s.ticker for s in self.signals}
        if self.benchmark:
            tickers.add(self.benchmark)
        if self.prefetched_prices is not None:
            prices = self.prefetched_prices
        else:
            prices = _fetch_prices(tickers, sd.isoformat(), ed.isoformat())

        # Trading calendar = union of all dates seen in prices, sorted
        all_dates = sorted({d.date() for df in prices.values() for d in df.index})
        date_index = {d: i for i, d in enumerate(all_dates)}

        # Build trades: walk bars from T+1 forward, trigger earliest exit condition.
        trades: list[Trade] = []
        skipped = {"no_prices": 0, "no_future_bar": 0, "insufficient_bars": 0,
                   "bad_price": 0, "ticker_already_open": 0,
                   "daily_cap": 0}
        # Track unique tickers that had no prices — the survivorship-bias tell.
        no_price_tickers: set[str] = set()
        slip = self.slippage_bps / 10000.0
        commission = float(self.commission_per_trade or 0.0)
        # Apply daily cap: rank same-day signals by strength (desc), keep top-K.
        # Cap applies per signal_date — not per entry_date — because that's
        # what the user actually observes when deciding what to act on.
        sorted_signals = sorted(self.signals, key=lambda s: s.signal_date)
        if self.max_signals_per_day and self.max_signals_per_day > 0:
            by_day: dict[str, list] = {}
            for s in sorted_signals:
                by_day.setdefault(s.signal_date, []).append(s)
            kept: list = []
            for day, group in by_day.items():
                group.sort(key=lambda s: -s.strength)
                kept.extend(group[: self.max_signals_per_day])
                skipped["daily_cap"] += max(0, len(group) - self.max_signals_per_day)
            sorted_signals = sorted(kept, key=lambda s: s.signal_date)

        # Per-ticker annualised volatility (for vol_target sizing). Computed
        # from the prefetched prices once per run.
        def _ticker_vol(tic: str) -> float:
            tdf = prices.get(tic.upper())
            if tdf is None or "Close" not in tdf.columns or len(tdf) < 30:
                return 0.0
            closes = tdf["Close"].dropna().to_numpy()
            if len(closes) < 30:
                return 0.0
            rets_ = (closes[1:] / closes[:-1]) - 1
            import math as _m
            mean = float(rets_.mean())
            var = float(((rets_ - mean) ** 2).sum() / max(1, len(rets_) - 1))
            return _m.sqrt(max(var, 0.0)) * _m.sqrt(252)

        # Median of signal strengths — baseline for strength_weighted sizing so
        # a typical-strength signal gets position_pct × equity, strongest ~cap ×.
        import statistics as _stats
        _s_vals = [float(s.strength) for s in sorted_signals if s.strength and s.strength > 0]
        median_strength = _stats.median(_s_vals) if _s_vals else 1.0

        open_by_ticker: dict[str, list[tuple[str, str]]] = {}
        _ticker_vol_cache: dict[str, float] = {}
        for s in sorted_signals:
            df = prices.get(s.ticker.upper())
            if df is None or df.empty:
                skipped["no_prices"] += 1
                no_price_tickers.add(s.ticker.upper())
                continue
            try:
                sig_dt = dt.date.fromisoformat(s.signal_date)
            except ValueError:
                continue
            # One-position-per-ticker gating: check if any prior trade on this
            # ticker has not yet exited by signal date.
            if self.one_position_per_ticker:
                live = open_by_ticker.get(s.ticker.upper(), [])
                if any(
                    dt.date.fromisoformat(entry_d) <= sig_dt <= dt.date.fromisoformat(exit_d)
                    for entry_d, exit_d in live
                ):
                    skipped["ticker_already_open"] += 1
                    continue
            # Entry = first bar strictly after signal date for this ticker
            tdates = df.index.date
            future_mask = tdates > sig_dt
            if not future_mask.any():
                skipped["no_future_bar"] += 1
                continue
            entry_i = int(future_mask.argmax())  # first True index
            # Must have at least hold_days of data after entry so the trade can
            # actually complete its intended hold — otherwise skip rather than
            # label a 1-day hold as a 'time' exit.
            if entry_i + self.hold_days >= len(df):
                skipped["insufficient_bars"] += 1
                continue
            entry_row_dt = tdates[entry_i]
            entry_price = float(df["Close"].iloc[entry_i])
            if entry_price <= 0:
                skipped["bad_price"] += 1
                continue
            shares = self.position_size / entry_price

            # Precompute stop / target prices
            d_sign = s.direction  # +1 long, -1 short
            stop_price = (
                entry_price * (1 - self.stop_loss_pct * d_sign)
                if self.stop_loss_pct
                else None
            )
            target_price = (
                entry_price * (1 + self.profit_target_pct * d_sign)
                if self.profit_target_pct
                else None
            )
            # Trailing stop: track most favourable price since entry
            best_for_trade = entry_price

            exit_i = min(entry_i + self.hold_days, len(df) - 1)
            exit_reason = "time"
            chosen_i = exit_i
            chosen_price: float | None = None

            # Walk bars from entry_i+1 through exit_i, first-hit wins
            for i in range(entry_i + 1, exit_i + 1):
                hi = float(df["High"].iloc[i]) if "High" in df.columns else float(df["Close"].iloc[i])
                lo = float(df["Low"].iloc[i]) if "Low" in df.columns else float(df["Close"].iloc[i])
                close = float(df["Close"].iloc[i])

                # Update trailing extreme (using the more favourable of high/low)
                if d_sign > 0:
                    best_for_trade = max(best_for_trade, hi)
                    trailing_price = (
                        best_for_trade * (1 - self.trailing_stop_pct)
                        if self.trailing_stop_pct
                        else None
                    )
                else:
                    best_for_trade = min(best_for_trade, lo)
                    trailing_price = (
                        best_for_trade * (1 + self.trailing_stop_pct)
                        if self.trailing_stop_pct
                        else None
                    )

                # Check exits on this bar (pessimistic: stop before target if same bar)
                if d_sign > 0:
                    if stop_price is not None and lo <= stop_price:
                        chosen_i, chosen_price, exit_reason = i, stop_price, "stop"
                        break
                    if trailing_price is not None and lo <= trailing_price:
                        chosen_i, chosen_price, exit_reason = i, trailing_price, "trailing"
                        break
                    if target_price is not None and hi >= target_price:
                        chosen_i, chosen_price, exit_reason = i, target_price, "target"
                        break
                else:
                    # short: stop is ABOVE entry, target is BELOW
                    if stop_price is not None and hi >= stop_price:
                        chosen_i, chosen_price, exit_reason = i, stop_price, "stop"
                        break
                    if trailing_price is not None and hi >= trailing_price:
                        chosen_i, chosen_price, exit_reason = i, trailing_price, "trailing"
                        break
                    if target_price is not None and lo <= target_price:
                        chosen_i, chosen_price, exit_reason = i, target_price, "target"
                        break

            if chosen_price is None:
                chosen_price = float(df["Close"].iloc[chosen_i])

            exit_row_dt = tdates[chosen_i]

            # Apply slippage: longs buy higher, sell lower; shorts reversed.
            # shares were sized at the un-slipped entry price already; the
            # slipped price only affects pnl/return (realistic fill cost).
            fill_entry = entry_price * (1 + slip * d_sign) if slip else entry_price
            fill_exit = chosen_price * (1 - slip * d_sign) if slip else chosen_price
            # Commissions: $commission on entry + $commission on exit.
            pnl = d_sign * shares * (fill_exit - fill_entry) - (2 * commission)
            ret = pnl / self.position_size if self.position_size else 0.0
            trades.append(
                Trade(
                    ticker=s.ticker.upper(),
                    signal_date=s.signal_date,
                    entry_date=entry_row_dt.isoformat(),
                    entry_price=fill_entry,
                    exit_date=exit_row_dt.isoformat(),
                    exit_price=fill_exit,
                    exit_reason=exit_reason,
                    direction=d_sign,
                    shares=shares,
                    position_value=self.position_size,
                    pnl=pnl,
                    return_pct=ret,
                    label=s.label,
                    strength=float(s.strength),
                )
            )
            if self.one_position_per_ticker:
                open_by_ticker.setdefault(s.ticker.upper(), []).append(
                    (entry_row_dt.isoformat(), exit_row_dt.isoformat())
                )

        # Daily MTM portfolio — show only the period when at least one trade is live.
        # Signals with long lead-in or late-window gaps would otherwise produce long
        # flat tails that dominate the chart.
        if trades:
            t_first = min(dt.date.fromisoformat(t.entry_date) for t in trades)
            t_last = max(dt.date.fromisoformat(t.exit_date) for t in trades)
            window_start = max(t_first, dt.date.fromisoformat(self.start)) if all_dates else t_first
            window_end = min(t_last, dt.date.fromisoformat(self.end)) if all_dates else t_last
        else:
            window_start = max(all_dates[0], dt.date.fromisoformat(self.start)) if all_dates else dt.date.fromisoformat(self.start)
            window_end = min(all_dates[-1], dt.date.fromisoformat(self.end)) if all_dates else dt.date.fromisoformat(self.end)
        window_dates = [d for d in all_dates if window_start <= d <= window_end]

        # Starting cash: fixed-$ mode sizes to cover max concurrent positions;
        # pct mode can start with whatever arbitrary capital — the curve compounds.
        if self.starting_cash is not None:
            starting_cash = self.starting_cash
        elif self.position_mode in ("pct_portfolio", "strength_weighted", "target_deployment",
                                    "risk_per_trade", "vol_target"):
            # Compounding modes: seed with an arbitrary base; curve scales freely.
            starting_cash = 100_000.0
        else:
            max_concurrent = _max_concurrent(trades)
            starting_cash = max(self.position_size * max_concurrent, self.position_size)

        cash = starting_cash
        open_positions: list[Trade] = []
        remaining_trades = sorted(trades, key=lambda t: t.entry_date)
        ti = 0
        equity_rows: list[tuple[dt.date, float, float, float]] = []

        for d in window_dates:
            # Close positions whose exit_date == d FIRST so freed cash is
            # available for new entries on the same day.
            still_open: list[Trade] = []
            for t in open_positions:
                if t.exit_date == d.isoformat():
                    cash += t.position_value + t.pnl
                else:
                    still_open.append(t)
            open_positions = still_open

            # Open new positions whose entry_date == d. Size each trade
            # against current equity unless we're in fixed_usd mode.
            while ti < len(remaining_trades) and remaining_trades[ti].entry_date == d.isoformat():
                t = remaining_trades[ti]
                if self.position_mode in ("pct_portfolio", "risk_per_trade", "vol_target", "strength_weighted", "target_deployment", "kelly"):
                    # Compute current equity at this moment (pre-entry)
                    current_invested = 0.0
                    for op in open_positions:
                        opdf = prices.get(op.ticker)
                        if opdf is None:
                            current_invested += op.position_value
                            continue
                        pr = opdf.loc[opdf.index.date == d, "Close"]
                        if not pr.empty:
                            cur = float(pr.iloc[0])
                            mtm = op.shares * (cur - op.entry_price) * op.direction
                            current_invested += op.position_value + mtm
                        else:
                            current_invested += op.position_value
                    current_equity = cash + current_invested
                    if self.position_mode == "pct_portfolio":
                        new_position_value = max(0.0, current_equity * self.position_pct)
                    elif self.position_mode == "risk_per_trade":
                        # Risk-per-trade needs a stop; fall back to pct_portfolio without one.
                        stop = self.stop_loss_pct or 0.0
                        if stop > 0:
                            new_position_value = max(
                                0.0, (current_equity * self.risk_per_trade_pct) / stop
                            )
                        else:
                            new_position_value = max(0.0, current_equity * self.position_pct)
                    elif self.position_mode == "vol_target":
                        vol = _ticker_vol_cache.get(t.ticker) or 0.0
                        if vol <= 0.0:
                            vol = _ticker_vol(t.ticker)
                            _ticker_vol_cache[t.ticker] = vol
                        if vol > 0:
                            new_position_value = max(
                                0.0, current_equity * self.target_vol_pct / vol
                            )
                        else:
                            new_position_value = max(0.0, current_equity * self.position_pct)
                    elif self.position_mode == "strength_weighted":
                        # Size proportional to signal strength. median-strength
                        # signal ≈ position_pct × equity. Clamp extremes so
                        # outliers don't blow up the book.
                        ratio = (t.strength / median_strength) if median_strength > 0 else 1.0
                        cap = max(1.0, float(self.strength_scale_cap or 3.0))
                        ratio = max(1.0 / cap, min(cap, ratio))
                        new_position_value = max(0.0, current_equity * self.position_pct * ratio)
                    elif self.position_mode == "target_deployment":
                        # Close the gap to target % invested, but cap per-
                        # position exposure at position_pct × equity so one
                        # signal on a clean book can't hog the whole portfolio.
                        target_invested = current_equity * max(0.0, min(1.0, self.target_deployment_pct))
                        gap = max(0.0, target_invested - current_invested)
                        per_position_cap = current_equity * self.position_pct
                        new_position_value = min(gap, per_position_cap)
                    else:  # kelly
                        # Fractional-Kelly sizing from *realized* trades in
                        # this backtest so far. f* = p - (1-p)/b where p is
                        # win rate and b is win/loss ratio. Multiply by the
                        # user-set kelly_fraction (typically 0.25 = quarter-
                        # Kelly to avoid blow-ups from parameter error).
                        closed = [tr for tr in open_positions if False]  # placeholder; use trades built
                        # Use prior trades from `trades` list (already has return_pct filled)
                        prior_wins = [tr.return_pct for tr in trades if tr.return_pct > 0]
                        prior_losses = [abs(tr.return_pct) for tr in trades if tr.return_pct <= 0 and tr.return_pct is not None]
                        if len(prior_wins) + len(prior_losses) >= 30:
                            p = len(prior_wins) / max(1, len(prior_wins) + len(prior_losses))
                            avg_win = sum(prior_wins) / max(1, len(prior_wins)) if prior_wins else 0.0
                            avg_loss = sum(prior_losses) / max(1, len(prior_losses)) if prior_losses else 0.01
                            b = (avg_win / avg_loss) if avg_loss > 0 else 1.0
                            f_star = p - (1 - p) / b if b > 0 else 0.0
                            f = max(0.0, f_star) * float(self.kelly_fraction or 0.25)
                            # Cap at position_pct × 5 so Kelly can't go crazy
                            f = min(f, self.position_pct * 5)
                            new_position_value = max(0.0, current_equity * f)
                        else:
                            # Not enough priors — fall back to base position_pct
                            new_position_value = max(0.0, current_equity * self.position_pct)

                    # Correlation-aware shrinkage: if the new ticker is highly
                    # correlated with already-open positions, we're not adding
                    # diversification — size down. Stacks on top of any mode.
                    if (
                        self.correlation_adjust
                        and self.correlation_shrinkage > 0
                        and open_positions
                        and new_position_value > 0
                    ):
                        lookback = max(10, int(self.correlation_lookback or 60))
                        new_df = prices.get(t.ticker.upper())
                        if new_df is not None and "Close" in new_df.columns:
                            new_close = new_df["Close"].loc[
                                new_df["Close"].index.date <= d
                            ].tail(lookback + 1)
                            import numpy as _np
                            new_rets = _np.diff(new_close.to_numpy()) / new_close.to_numpy()[:-1]
                            if len(new_rets) >= 10:
                                corrs: list[float] = []
                                for op in open_positions:
                                    op_df = prices.get(op.ticker.upper())
                                    if op_df is None or "Close" not in op_df.columns:
                                        continue
                                    op_close = op_df["Close"].loc[
                                        op_df["Close"].index.date <= d
                                    ].tail(lookback + 1)
                                    op_rets = _np.diff(op_close.to_numpy()) / op_close.to_numpy()[:-1]
                                    n_align = min(len(new_rets), len(op_rets))
                                    if n_align < 10:
                                        continue
                                    a = new_rets[-n_align:]
                                    b = op_rets[-n_align:]
                                    denom = float(_np.std(a) * _np.std(b))
                                    if denom > 0:
                                        cov = float(_np.mean((a - a.mean()) * (b - b.mean())))
                                        corrs.append(cov / denom)
                                if corrs:
                                    avg_corr = sum(corrs) / len(corrs)
                                    # Only shrink on positive correlation
                                    shrink = 1.0 - max(0.0, avg_corr) * float(self.correlation_shrinkage)
                                    shrink = max(0.1, min(1.0, shrink))
                                    new_position_value *= shrink

                    # Sector cap: skip if opening would push sector over max_sector_pct.
                    if (
                        self.max_sector_pct is not None
                        and self.max_sector_pct > 0
                        and self.sector_lookup
                    ):
                        sector = (self.sector_lookup or {}).get(t.ticker, "")
                        if sector:
                            # Current exposure per sector (from open positions)
                            sec_exposure = 0.0
                            for op in open_positions:
                                if (self.sector_lookup or {}).get(op.ticker, "") == sector:
                                    sec_exposure += op.position_value
                            if (
                                (sec_exposure + new_position_value) / max(current_equity, 1.0)
                                > self.max_sector_pct
                            ):
                                skipped["daily_cap"] += 0  # sector-capped, silent skip
                                skipped.setdefault("sector_cap", 0)
                                skipped["sector_cap"] += 1
                                ti += 1
                                continue
                    # Clip to available cash — prevents implicit leverage
                    # (e.g. a burst of same-day signals in strength_weighted
                    # each wanting N% of equity but only 1% of cash left).
                    new_position_value = max(0.0, min(new_position_value, max(0.0, cash)))
                    # Rescale shares / pnl. return_pct is size-invariant, keep.
                    new_shares = new_position_value / t.entry_price if t.entry_price > 0 else 0.0
                    t.shares = new_shares
                    t.position_value = new_position_value
                    t.pnl = t.direction * new_shares * (t.exit_price - t.entry_price)
                # Skip microscopic positions (after cash-clip or sector-cap scaling)
                if t.position_value < 100:
                    skipped.setdefault("too_small", 0)
                    skipped["too_small"] += 1
                    ti += 1
                    continue
                cash -= t.position_value
                open_positions.append(t)
                ti += 1

            # MTM value of open positions at today's close (for equity curve)
            invested = 0.0
            for t in open_positions:
                tdf = prices.get(t.ticker)
                if tdf is None:
                    invested += t.position_value
                    continue
                todays = tdf.loc[tdf.index.date == d, "Close"]
                if not todays.empty:
                    cur_price = float(todays.iloc[0])
                    mtm = t.shares * (cur_price - t.entry_price) * t.direction
                    invested += t.position_value + mtm
                else:
                    invested += t.position_value
            equity_rows.append((d, cash + invested, cash, invested))

        eq = pd.DataFrame(equity_rows, columns=["date", "equity", "cash", "invested"])

        # Benchmark: buy-and-hold of benchmark from window start
        bench_df = None
        if self.benchmark and self.benchmark.upper() in prices:
            bdf = prices[self.benchmark.upper()]
            base_price = None
            rows = []
            for d in window_dates:
                match = bdf.loc[bdf.index.date == d, "Close"]
                if match.empty:
                    continue
                p = float(match.iloc[0])
                if base_price is None:
                    base_price = p
                rows.append((d, starting_cash * (p / base_price)))
            bench_df = pd.DataFrame(rows, columns=["date", "equity"])

        # Drop phantom trades — signals that got resized below $100 by the
        # cash-clip and were never added to open_positions. Their
        # position_value is effectively zero but their original return_pct
        # (computed against the default position_size during initial trade
        # construction) is still populated, which would inflate win rate and
        # average-return metrics. Filter them here before summarising.
        trades = [t for t in trades if (t.position_value or 0) >= 100.0]

        # Metrics
        from backtester.metrics import summarise  # local import avoids circular
        summary = summarise(trades, eq, bench_df, starting_cash)
        summary["n_signals"] = len(self.signals)
        summary["skipped_signals"] = skipped
        summary["max_concurrent_positions"] = _max_concurrent(trades) if trades else 0
        # Survivorship-bias report: unique tickers the signal fired on that we
        # have no price data for. Typically these are delisted / acquired names
        # that yfinance silently drops — their signals would have lost or won
        # but we'll never know. Set TIINGO_API_KEY to resolve from Tiingo's
        # delisted archive.
        summary["survivorship"] = {
            "n_excluded_signals": int(skipped["no_prices"]),
            "n_excluded_tickers": len(no_price_tickers),
            "excluded_tickers": sorted(no_price_tickers)[:50],  # cap at 50 for response size
            "pct_of_total": (skipped["no_prices"] / max(1, len(self.signals))),
        }

        # Signal half-life: raw signal-level forward-return decay curve,
        # independent of strategy sizing / stops. Shows when the alpha
        # actually lives vs when it's just holding beta.
        try:
            from backtester.halflife import compute_halflife as _compute_halflife
            hl = _compute_halflife(self.signals, prices, max_days=60)
            if hl:
                summary["halflife"] = hl
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("seclab").warning(f"halflife skipped: {_e}")

        # Information Coefficient — does signal.strength actually rank
        # forward returns? Spearman rank correlation per horizon.
        try:
            from backtester.ic import compute_ic as _compute_ic
            ic = _compute_ic(self.signals, prices)
            if ic:
                summary["information_coefficient"] = ic
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("seclab").warning(f"IC skipped: {_e}")

        # Turnover + Transaction Cost Attribution (TCA)
        # Slippage cost per trade ≈ 2 × position_value × slip (entry + exit)
        # Commission cost per trade = 2 × commission
        # Turnover (annualized) = total notional traded / avg equity / years
        if trades:
            total_slippage_usd = sum(2 * t.position_value * slip for t in trades) if slip else 0.0
            total_commission_usd = 2 * commission * len(trades)
            total_cost_usd = total_slippage_usd + total_commission_usd
            gross_notional = sum(2 * t.position_value for t in trades)  # entry + exit
            # Years elapsed across the equity curve
            years = max(len(eq) / 252.0, 1e-6) if not eq.empty else 1e-6
            avg_equity = float(eq["equity"].mean()) if not eq.empty else starting_cash
            turnover_ratio_ann = gross_notional / max(avg_equity, 1.0) / years
            # What % of gross returns did costs eat?
            final_equity_val = float(eq.iloc[-1]["equity"]) if not eq.empty else starting_cash
            net_profit = final_equity_val - starting_cash
            gross_profit = net_profit + total_cost_usd
            cost_drag_pct = (total_cost_usd / max(abs(gross_profit), 1.0)) if gross_profit != 0 else 0.0
            summary["costs"] = {
                "slippage_usd": total_slippage_usd,
                "commission_usd": total_commission_usd,
                "total_cost_usd": total_cost_usd,
                "cost_drag_pct_of_gross": cost_drag_pct,
                "slippage_bps": float(self.slippage_bps),
                "commission_per_trade": float(self.commission_per_trade or 0.0),
            }
            summary["turnover"] = {
                "gross_notional_usd": gross_notional,
                "avg_equity_usd": avg_equity,
                "years_elapsed": years,
                "annualized_ratio": turnover_ratio_ann,
            }
        return BacktestResult(trades=trades, equity_curve=eq, benchmark_curve=bench_df, summary=summary)


def _max_concurrent(trades: list[Trade]) -> int:
    """Compute the maximum number of simultaneously-open positions."""
    events: list[tuple[str, int]] = []
    for t in trades:
        events.append((t.entry_date, +1))
        events.append((t.exit_date, -1))
    events.sort()
    cur = 0
    peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak or 1

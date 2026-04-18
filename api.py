"""FastAPI backend for secLab dashboards."""
from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()  # pick up FINNHUB_API_KEY etc.

# ─── Logging setup ─────────────────────────────────────────────────────────
logger = logging.getLogger("seclab")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(_h)
    logger.propagate = False

# In-flight request tracker — lets /status show what's running right now
_inflight: dict[str, dict[str, Any]] = {}
_inflight_lock = threading.Lock()


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Log each request with a unique ID, timing, and make it visible in /status."""

    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:8]
        path_q = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
        started = time.perf_counter()
        with _inflight_lock:
            _inflight[rid] = {
                "id": rid,
                "method": request.method,
                "path": path_q,
                "started_at": time.time(),
            }
        logger.info(f"→ {rid} {request.method} {path_q}")
        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(f"FAIL {rid} {request.method} {path_q} raised {type(e).__name__}: {e}")
            with _inflight_lock:
                _inflight.pop(rid, None)
            raise
        elapsed = time.perf_counter() - started
        with _inflight_lock:
            _inflight.pop(rid, None)
        logger.info(f"← {rid} {response.status_code} in {elapsed:.2f}s  {path_q}")
        return response


from backtester.core import Backtest
from backtester.signals import PRESETS
from backtester.sweep import run_sweep as run_backtest_sweep
from backtester.walkforward import run_walkforward as run_backtest_walkforward
from backtester.baseline import run_random_baseline
from backtester.factors import attribute_returns, attribute_returns_ff5
from backtester.garch import fit_garch_1_1
from backtester.ic import compute_ic
from backtester.mc import simulate_paths
from backtester import forward as paper
from data_store import (
    compute_ratios_snapshot,
    connect,
    ensure_8k_window,
    ensure_form144_window,
    ensure_fundamentals_quarters,
    ensure_insider_quarters,
    ensure_late_filings_window,
    ensure_13g_window,
    ensure_delistings_window,
    ensure_foreign_filings_window,
    ensure_formD_window,
    ensure_form3_window,
    ensure_proxy_contests_window,
    ensure_secondaries_window,
    ensure_tender_offers_window,
    ensure_thirteen_d_window,
    get_dark_pool_latest,
    get_dark_pool_ticker,
    get_dark_pool_spikes,
    get_offexchange_latest,
    get_offexchange_ticker,
    get_offexchange_spikes,
    get_short_sale_latest,
    get_short_sale_ticker,
    get_short_sale_spikes,
    get_smart_vs_dumb,
    get_smart_vs_dumb_ticker,
    get_ftd_latest,
    get_ftd_ticker,
    get_sho_threshold_latest,
    get_short_interest_latest,
    get_short_interest_ticker,
    get_ipo_pipeline,
    get_ipo_recent,
    get_ipo_lockups,
    get_merger_filings,
    get_13g_filings,
    get_8k_events,
    get_activism_filings,
    get_delistings,
    get_foreign_filings,
    get_formD_filings,
    get_form3_filings,
    get_fund_latest_holdings,
    get_fundamentals,
    get_insider_purchases,
    get_key_ratios,
    get_late_filings,
    get_planned_sells,
    get_proxy_contests,
    get_secondaries,
    get_tender_offers,
    get_ticker_dossier,
    ingest_all_funds,
    ingest_fund_13f,
    list_funds,
    refresh_ticker_map,
    screener_query,
    search_tickers,
    seed_fund_meta,
    ticker_to_cik,
)
from providers.form_8k import ITEM_LABELS as FORM_8K_ITEM_LABELS, DEFAULT_INTERESTING as FORM_8K_DEFAULT
from providers import finnhub, prices, alpaca_live

app = FastAPI(title="secLab API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)


def _bdays_ago(n: int) -> date:
    d = date.today()
    step = 0
    while step < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            step += 1
    return d


def _filings_scanned(start: str, end: str) -> int:
    con = connect()
    row = con.execute(
        """
        SELECT COUNT(*) FROM insider_submission
        WHERE DOCUMENT_TYPE IN ('4','4/A')
          AND FILING_DATE BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()
    con.close()
    return int(row[0]) if row else 0


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "providers": {
            "finnhub": finnhub.enabled(),
            "tiingo": prices.tiingo_enabled(),
            "alpaca_live": alpaca_live.enabled(),
        },
    }


@app.get("/status")
def status() -> dict[str, Any]:
    """Show currently in-flight requests so the UI can diagnose 'stuck' calls."""
    now = time.time()
    with _inflight_lock:
        snapshot = [
            {**r, "elapsed_s": round(now - r["started_at"], 2)}
            for r in _inflight.values()
        ]
    return {"in_flight": sorted(snapshot, key=lambda x: -x["elapsed_s"])}


@app.get("/signals/insider-buying")
def insider_buying(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    top: int = Query(25, ge=1, le=500),
    min_value: float = Query(0, ge=0),
) -> dict[str, Any]:
    start = start or _bdays_ago(3).isoformat()
    end = end or _bdays_ago(1).isoformat()

    coverage = ensure_insider_quarters(start, end)
    result = get_insider_purchases(start, end, top=top, min_value=min_value)

    warnings: list[str] = []
    if coverage["missing_current"]:
        if finnhub.enabled():
            warnings.append(
                f"Window touches current quarter {coverage['missing_current']} "
                "— Finnhub fallback is available but needs a ticker universe. "
                "Hit /signals/insider-buying/live?symbol=TICKER for single-ticker lookups."
            )
        else:
            warnings.append(
                f"Window touches current quarter {coverage['missing_current']}, "
                "which DERA hasn't released yet. Set FINNHUB_API_KEY in .env to "
                "enable the live-provider fallback for today's filings."
            )

    return {
        "range": {"start": start, "end": end},
        "coverage": coverage,
        "warnings": warnings,
        "providers": {"finnhub": finnhub.enabled()},
        "summary": {
            "filings_scanned": _filings_scanned(start, end),
            **result["summary"],
        },
        "leaderboard": result["leaderboard"],
        "transactions": result["transactions"],
    }


@app.get("/backtest/presets")
def backtest_presets() -> dict[str, Any]:
    return {"presets": [{"key": k, "label": v["label"]} for k, v in PRESETS.items()]}


@app.get("/backtest/sweep")
def backtest_sweep(
    preset: str = Query(..., description="Single preset, or comma-separated list"),
    start: str = Query(...),
    end: str = Query(...),
    hold_days: str = Query("10,20,40", description="Comma-separated ints"),
    stop_loss_pct: str = Query("0,0.05,0.08,0.10", description="Comma-separated fractions"),
    profit_target_pct: str = Query("0,0.15,0.25", description="Comma-separated fractions"),
    trailing_stop_pct: str = Query("0", description="Comma-separated fractions"),
    position_mode: str = Query("fixed_usd"),
    position_size: float = Query(10_000, ge=100),
    position_pct: float = Query(0.02, ge=0.0001, le=1.0),
    target_deployment_pct: float = Query(0.80, ge=0.0, le=1.0),
    strength_scale_cap: float = Query(3.0, ge=1.0, le=10.0),
    benchmark: str = Query("SPY"),
    slippage_bps: float = Query(0.0, ge=0, le=500),
    commission_per_trade: float = Query(0.0, ge=0, le=100),
    one_position_per_ticker: bool = Query(False),
) -> dict[str, Any]:
    def _parse(s: str, cast) -> list:
        return [cast(x.strip()) for x in s.split(",") if x.strip()]

    return run_backtest_sweep(
        preset=preset,
        start=start,
        end=end,
        hold_days=_parse(hold_days, int),
        stop_loss_pct=_parse(stop_loss_pct, float),
        profit_target_pct=_parse(profit_target_pct, float),
        trailing_stop_pct=_parse(trailing_stop_pct, float),
        position_size=position_size,
        benchmark=benchmark,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        one_position_per_ticker=one_position_per_ticker,
        position_mode=position_mode,
        position_pct=position_pct,
        target_deployment_pct=target_deployment_pct,
        strength_scale_cap=strength_scale_cap,
    )


@app.get("/backtest/walkforward")
def backtest_walkforward(
    preset: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    hold_days: str = Query("10,20,40"),
    stop_loss_pct: str = Query("0,0.05,0.08,0.10"),
    profit_target_pct: str = Query("0,0.15,0.25"),
    trailing_stop_pct: str = Query("0"),
    n_folds: int = Query(2, ge=2, le=8),
    top_n: int = Query(5, ge=1, le=20),
    rank_by: str = Query("sharpe"),
    position_mode: str = Query("fixed_usd"),
    position_size: float = Query(10_000, ge=100),
    position_pct: float = Query(0.02, ge=0.0001, le=1.0),
    benchmark: str = Query("SPY"),
    slippage_bps: float = Query(0.0, ge=0, le=500),
    commission_per_trade: float = Query(0.0, ge=0, le=100),
    one_position_per_ticker: bool = Query(False),
) -> dict[str, Any]:
    def _parse(s: str, cast) -> list:
        return [cast(x.strip()) for x in s.split(",") if x.strip()]

    return run_backtest_walkforward(
        preset=preset, start=start, end=end,
        hold_days=_parse(hold_days, int),
        stop_loss_pct=_parse(stop_loss_pct, float),
        profit_target_pct=_parse(profit_target_pct, float),
        trailing_stop_pct=_parse(trailing_stop_pct, float),
        n_folds=n_folds, top_n=top_n, rank_by=rank_by,
        position_size=position_size, benchmark=benchmark,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        one_position_per_ticker=one_position_per_ticker,
        position_mode=position_mode,
        position_pct=position_pct,
    )


@app.get("/backtest/run")
def backtest_run(
    preset: str = Query(..., description="Signal preset key"),
    start: str = Query(..., description="Signal window start YYYY-MM-DD"),
    end: str = Query(..., description="Signal window end YYYY-MM-DD"),
    hold_days: int = Query(20, ge=1, le=252),
    position_mode: str = Query("fixed_usd", description="'fixed_usd', 'pct_portfolio', 'risk_per_trade', 'vol_target', 'strength_weighted', 'target_deployment'"),
    position_size: float = Query(10_000, ge=100),
    position_pct: float = Query(0.02, ge=0.0001, le=1.0, description="Fraction of portfolio per trade (also per-position cap in strength_weighted/target_deployment)."),
    target_deployment_pct: float = Query(0.80, ge=0.0, le=1.0, description="target_deployment mode: keep portfolio this % invested."),
    strength_scale_cap: float = Query(3.0, ge=1.0, le=10.0, description="strength_weighted mode: clamp position multiplier to [1/cap, cap]."),
    benchmark: str = Query("SPY"),
    stop_loss_pct: float = Query(0.0, ge=0, le=1, description="e.g. 0.10 = -10% stop. 0 disables."),
    profit_target_pct: float = Query(0.0, ge=0, le=5, description="e.g. 0.20 = +20% target. 0 disables."),
    trailing_stop_pct: float = Query(0.0, ge=0, le=1, description="e.g. 0.08 = 8% trailing. 0 disables."),
    slippage_bps: float = Query(0.0, ge=0, le=500, description="Basis points applied at entry and exit fills."),
    commission_per_trade: float = Query(0.0, ge=0, le=100, description="Flat $ per fill (2× per trade)."),
    one_position_per_ticker: bool = Query(False, description="Suppress duplicate signals on a ticker while a position is open."),
    risk_per_trade_pct: float = Query(0.01, ge=0.0001, le=0.5),
    target_vol_pct: float = Query(0.10, ge=0.01, le=1.0),
    max_signals_per_day: int = Query(0, ge=0, le=200, description="0 = uncapped; else keep top-N by signal strength per day."),
    max_sector_pct: float = Query(0.0, ge=0, le=1.0, description="0 = off; cap any single GICS sector as a fraction of equity."),
    correlation_adjust: bool = Query(False, description="Shrink new positions that are correlated with open positions."),
    correlation_shrinkage: float = Query(0.5, ge=0, le=1.0),
    correlation_lookback: int = Query(60, ge=10, le=252),
) -> dict[str, Any]:
    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        return {"error": f"unknown preset {preset}", "available": list(PRESETS)}
    signals = preset_cfg["fn"](start=start, end=end)
    # Sector lookup — built lazily from yfinance info if sector cap is active.
    sector_lookup: dict[str, str] | None = None
    if max_sector_pct > 0:
        tickers_in_signals = {s.ticker for s in signals}
        sector_lookup = {}
        for tic in tickers_in_signals:
            try:
                info = prices.get_info(tic)
                sec = (info or {}).get("sector") or ""
                if sec:
                    sector_lookup[tic] = sec
            except Exception:
                pass

    allowed_modes = ("fixed_usd", "pct_portfolio", "risk_per_trade", "vol_target",
                     "strength_weighted", "target_deployment")
    bt = Backtest(
        signals=signals,
        start=start,
        end=end,
        hold_days=hold_days,
        position_mode=position_mode if position_mode in allowed_modes else "fixed_usd",
        position_size=position_size,
        position_pct=position_pct,
        risk_per_trade_pct=risk_per_trade_pct,
        target_vol_pct=target_vol_pct,
        target_deployment_pct=target_deployment_pct,
        strength_scale_cap=strength_scale_cap,
        stop_loss_pct=stop_loss_pct or None,
        profit_target_pct=profit_target_pct or None,
        trailing_stop_pct=trailing_stop_pct or None,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
        one_position_per_ticker=one_position_per_ticker,
        max_signals_per_day=max_signals_per_day or None,
        max_sector_pct=max_sector_pct or None,
        sector_lookup=sector_lookup,
        correlation_adjust=correlation_adjust,
        correlation_shrinkage=correlation_shrinkage,
        correlation_lookback=correlation_lookback,
        benchmark=benchmark or None,
    )
    result = bt.run()
    out = result.to_json()
    out["preset"] = {"key": preset, "label": preset_cfg["label"]}

    # Fama-French factor attribution — how much of the return is just factor
    # exposure vs genuine residual alpha. Quiet failure if the factor CSV
    # can't be reached (Ken French's server occasionally 403s).
    _rets_cache: list[float] | None = None
    _dates_cache: list[str] | None = None
    try:
        eq = out.get("equity_curve") or []
        if len(eq) >= 30:
            _dates_cache = [e["date"] for e in eq[1:]]
            _rets_cache = [eq[i]["equity"] / eq[i - 1]["equity"] - 1 for i in range(1, len(eq))]
            attr = attribute_returns(_dates_cache, _rets_cache)
            if attr:
                out["factor_attribution"] = attr
    except Exception as e:
        logger.warning(f"factor attribution skipped: {e}")

    # GARCH(1,1) conditional volatility — time-varying σ on the strategy's
    # daily returns. Reveals vol clustering / regime changes that static
    # Sharpe misses. Runs on the same return series as FF above.
    try:
        if _rets_cache and len(_rets_cache) >= 60:
            garch = fit_garch_1_1(_rets_cache, dates=_dates_cache)
            if garch:
                out["garch"] = garch
    except Exception as e:
        logger.warning(f"garch skipped: {e}")

    # FF5 (5-factor) — parallel to FF3 attribution; captures quality / investment
    try:
        if _rets_cache and _dates_cache and len(_rets_cache) >= 60:
            ff5 = attribute_returns_ff5(_dates_cache, _rets_cache)
            if ff5:
                out["factor_attribution_ff5"] = ff5
    except Exception as e:
        logger.warning(f"ff5 skipped: {e}")

    # Monte Carlo forward-path envelope (bootstrap historical daily returns)
    try:
        if _rets_cache and len(_rets_cache) >= 60:
            mc = simulate_paths(_rets_cache, horizon_days=252, n_paths=1000)
            if mc and not mc.get("insufficient_data"):
                out["monte_carlo"] = mc
    except Exception as e:
        logger.warning(f"monte carlo skipped: {e}")

    # Beta-hedge view — post-hoc hedge via SPY short: r_hedged = r_strat - β × r_bench.
    # If FF attribution is present, default β to the market coefficient so the
    # default view matches "strip out market exposure."
    try:
        bc = out.get("benchmark_curve")
        if _rets_cache and bc and len(bc) >= len(_rets_cache) + 1:
            bench_rets = [bc[i]["equity"] / bc[i - 1]["equity"] - 1 for i in range(1, len(bc))]
            ff_beta = None
            fa = out.get("factor_attribution")
            if fa and "factors" in fa and "Mkt-RF" in fa["factors"]:
                ff_beta = fa["factors"]["Mkt-RF"]["beta"]
            from backtester.metrics import compute_beta_hedge
            out["beta_hedge"] = compute_beta_hedge(_rets_cache, bench_rets, beta=ff_beta)
    except Exception as e:
        logger.warning(f"beta-hedge skipped: {e}")
    out["params"] = {
        "start": start,
        "end": end,
        "hold_days": hold_days,
        "position_mode": position_mode,
        "position_size": position_size,
        "position_pct": position_pct,
        "benchmark": benchmark,
        "stop_loss_pct": stop_loss_pct,
        "profit_target_pct": profit_target_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "slippage_bps": slippage_bps,
        "commission_per_trade": commission_per_trade,
        "one_position_per_ticker": one_position_per_ticker,
        "risk_per_trade_pct": risk_per_trade_pct,
        "target_vol_pct": target_vol_pct,
        "target_deployment_pct": target_deployment_pct,
        "strength_scale_cap": strength_scale_cap,
        "max_signals_per_day": max_signals_per_day,
        "max_sector_pct": max_sector_pct,
        "correlation_adjust": correlation_adjust,
        "correlation_shrinkage": correlation_shrinkage,
        "correlation_lookback": correlation_lookback,
    }
    return out


@app.get("/backtest/random-baseline")
def backtest_random_baseline(
    preset: str = Query(..., description="Signal preset key"),
    start: str = Query(...),
    end: str = Query(...),
    n_iterations: int = Query(20, ge=5, le=200),
    hold_days: int = Query(20, ge=1, le=252),
    stop_loss_pct: float = Query(0.0, ge=0, le=1),
    profit_target_pct: float = Query(0.0, ge=0, le=5),
    position_mode: str = Query("fixed_usd"),
    position_size: float = Query(10_000, ge=100),
    position_pct: float = Query(0.02, ge=0.0001, le=1.0),
    slippage_bps: float = Query(0.0, ge=0, le=500),
    commission_per_trade: float = Query(0.0, ge=0, le=100),
) -> dict[str, Any]:
    """Permutation test: does the real preset beat randomised ticker-date pairings?"""
    return run_random_baseline(
        preset=preset,
        start=start,
        end=end,
        n_iterations=n_iterations,
        hold_days=hold_days,
        stop_loss_pct=stop_loss_pct,
        profit_target_pct=profit_target_pct,
        position_mode=position_mode if position_mode in ("fixed_usd", "pct_portfolio", "risk_per_trade", "vol_target") else "fixed_usd",
        position_size=position_size,
        position_pct=position_pct,
        slippage_bps=slippage_bps,
        commission_per_trade=commission_per_trade,
    )


@app.get("/research/halflife")
def research_halflife(
    presets: str = Query(..., description="Comma-separated preset keys"),
    start: str = Query(...),
    end: str = Query(...),
    max_days: int = Query(60, ge=5, le=252),
) -> dict[str, Any]:
    """Run half-life study on N presets over the same window. Overlays decay curves."""
    import datetime as _dt
    from backtester.core import _fetch_prices
    from backtester.halflife import compute_halflife

    preset_keys = [p.strip() for p in presets.split(",") if p.strip()]
    if not preset_keys:
        raise HTTPException(400, "no presets specified")

    sd = _dt.date.fromisoformat(start) - _dt.timedelta(days=60)
    ed = _dt.date.fromisoformat(end) + _dt.timedelta(days=max_days + 10)

    out: list[dict[str, Any]] = []
    for key in preset_keys:
        cfg = PRESETS.get(key)
        if not cfg:
            out.append({"preset": key, "error": f"unknown preset {key}"})
            continue
        signals = cfg["fn"](start=start, end=end)
        if not signals:
            out.append({"preset": key, "label": cfg["label"], "error": "0 signals"})
            continue
        tickers = {s.ticker.upper() for s in signals}
        prices = _fetch_prices(tickers, sd.isoformat(), ed.isoformat())
        hl = compute_halflife(signals, prices, max_days=max_days)
        if not hl:
            out.append({
                "preset": key, "label": cfg["label"],
                "error": "insufficient signals for halflife", "n_signals": len(signals),
            })
            continue
        hl["preset"] = key
        hl["label"] = cfg["label"]
        hl["n_signals_total"] = len(signals)
        out.append(hl)
    return {"start": start, "end": end, "max_days": max_days, "results": out}


@app.get("/signals/dark-pool")
def dark_pool_latest(top: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Most recent week's top tickers by FINRA ATS (dark pool) volume."""
    return get_dark_pool_latest(top=top)


@app.get("/signals/dark-pool/spikes")
def dark_pool_spikes(
    top: int = Query(30, ge=1, le=200),
    min_shares: int = Query(1_000_000, ge=0),
) -> dict[str, Any]:
    """Tickers with most-anomalous latest-week dark-pool shares vs 8-week median."""
    return get_dark_pool_spikes(top=top, min_shares=min_shares)


@app.get("/signals/dark-pool/{ticker}")
def dark_pool_ticker(ticker: str, weeks: int = Query(52, ge=4, le=260)) -> dict[str, Any]:
    """Per-ticker weekly time series + ATS breakdown."""
    return get_dark_pool_ticker(ticker, weeks=weeks)


@app.post("/signals/dark-pool/refresh")
def dark_pool_refresh(
    start: str | None = Query(None, description="Default: today − weeks × 7"),
    end: str | None = Query(None, description="Default: today"),
    weeks: int = Query(12, ge=1, le=52),
) -> dict[str, Any]:
    """Pull latest FINRA ATS weekly data. Idempotent — skips cached weeks."""
    from providers.finra_ats import ensure_dark_pool_window
    end = end or date.today().isoformat()
    start = start or (date.today() - timedelta(days=weeks * 7)).isoformat()
    return ensure_dark_pool_window(start=start, end=end)


# ─── Non-ATS (OTC wholesaler / retail-flow) endpoints ────────────────────────

@app.get("/signals/non-ats")
def non_ats_latest(top: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Most recent week's top tickers by OTC non-ATS (wholesaler) volume."""
    return get_offexchange_latest(venue_type="non_ats", top=top)


@app.get("/signals/non-ats/spikes")
def non_ats_spikes(
    top: int = Query(30, ge=1, le=200),
    min_shares: int = Query(1_000_000, ge=0),
) -> dict[str, Any]:
    return get_offexchange_spikes(top=top, min_shares=min_shares, venue_type="non_ats")


@app.get("/signals/non-ats/{ticker}")
def non_ats_ticker(ticker: str, weeks: int = Query(52, ge=4, le=260)) -> dict[str, Any]:
    return get_offexchange_ticker(ticker, weeks=weeks, venue_type="non_ats")


@app.post("/signals/non-ats/refresh")
def non_ats_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    weeks: int = Query(12, ge=1, le=52),
) -> dict[str, Any]:
    from providers.finra_ats import ensure_non_ats_window
    end = end or date.today().isoformat()
    start = start or (date.today() - timedelta(days=weeks * 7)).isoformat()
    return ensure_non_ats_window(start=start, end=end)


# ─── FINRA daily short-sale volume ───────────────────────────────────────────

@app.get("/signals/short-sale")
def short_sale_latest(
    top: int = Query(50, ge=1, le=500),
    min_total_volume: int = Query(100_000, ge=0),
) -> dict[str, Any]:
    """Latest day's top tickers by short % of total volume."""
    return get_short_sale_latest(top=top, min_total_volume=min_total_volume)


@app.get("/signals/short-sale/spikes")
def short_sale_spikes(
    top: int = Query(30, ge=1, le=200),
    min_total_volume: int = Query(1_000_000, ge=0),
) -> dict[str, Any]:
    """Tickers whose latest short % deviates most from trailing 20-day median."""
    return get_short_sale_spikes(top=top, min_total_volume=min_total_volume)


@app.get("/signals/short-sale/{ticker}")
def short_sale_ticker(
    ticker: str, days: int = Query(60, ge=5, le=500)
) -> dict[str, Any]:
    return get_short_sale_ticker(ticker, days=days)


@app.post("/signals/short-sale/refresh")
def short_sale_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int = Query(20, ge=1, le=500),
) -> dict[str, Any]:
    from providers.finra_short import ensure_short_sale_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=days)).isoformat()
    return ensure_short_sale_window(start=start_d, end=end_d)


# ─── Smart vs Dumb money ─────────────────────────────────────────────────────

@app.get("/signals/smart-vs-dumb")
def smart_vs_dumb_latest(
    week: str | None = Query(None, description="ISO week-start (Monday). Default: latest available."),
    top: int = Query(30, ge=5, le=200),
) -> dict[str, Any]:
    """Two leaderboards for the latest week common to ATS + non-ATS feeds:
    `smart` (institutional dark-pool routing dominant) and
    `dumb` (retail-flow wholesalers dominant), with insider Form 4 context."""
    return get_smart_vs_dumb(week=week, top=top)


@app.get("/signals/smart-vs-dumb/{ticker}")
def smart_vs_dumb_ticker(
    ticker: str, weeks: int = Query(26, ge=4, le=260)
) -> dict[str, Any]:
    return get_smart_vs_dumb_ticker(ticker, weeks=weeks)


# ─── FTDs / Reg SHO / Short interest (short-pressure) ─────────────────────────

@app.get("/signals/ftd")
def ftd_latest(
    top: int = Query(50, ge=1, le=500),
    min_notional: float = Query(1_000_000, ge=0),
) -> dict[str, Any]:
    return get_ftd_latest(top=top, min_notional=min_notional)


@app.get("/signals/ftd/{ticker}")
def ftd_ticker(ticker: str, days: int = Query(90, ge=5, le=500)) -> dict[str, Any]:
    return get_ftd_ticker(ticker, days=days)


@app.post("/signals/ftd/refresh")
def ftd_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    months: int = Query(3, ge=1, le=24),
) -> dict[str, Any]:
    from providers.sec_ftd import ensure_ftd_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=months * 31)).isoformat()
    return ensure_ftd_window(start=start_d, end=end_d)


@app.get("/signals/sho-threshold")
def sho_threshold_latest() -> dict[str, Any]:
    """FINRA Reg SHO threshold list for the latest available day."""
    return get_sho_threshold_latest()


@app.post("/signals/sho-threshold/refresh")
def sho_threshold_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int = Query(14, ge=1, le=180),
) -> dict[str, Any]:
    from providers.finra_short_meta import ensure_sho_threshold_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=days)).isoformat()
    return ensure_sho_threshold_window(start=start_d, end=end_d)


@app.get("/signals/short-interest")
def short_interest_latest(
    top: int = Query(50, ge=1, le=500),
    min_days_to_cover: float = Query(0, ge=0),
) -> dict[str, Any]:
    return get_short_interest_latest(top=top, min_days_to_cover=min_days_to_cover)


@app.get("/signals/short-interest/{ticker}")
def short_interest_ticker(ticker: str, periods: int = Query(26, ge=4, le=260)) -> dict[str, Any]:
    return get_short_interest_ticker(ticker, periods=periods)


@app.post("/signals/short-interest/refresh")
def short_interest_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    months: int = Query(3, ge=1, le=24),
) -> dict[str, Any]:
    from providers.finra_short_meta import ensure_short_interest_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=months * 31)).isoformat()
    return ensure_short_interest_window(start=start_d, end=end_d)


# ─── IPO tracker ─────────────────────────────────────────────────────────────

@app.get("/signals/ipos/pipeline")
def ipos_pipeline(
    days: int = Query(60, ge=7, le=365),
    top: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    return get_ipo_pipeline(days=days, top=top)


@app.get("/signals/ipos/recent")
def ipos_recent(
    days: int = Query(90, ge=7, le=365),
    top: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    return get_ipo_recent(days=days, top=top)


@app.get("/signals/ipos/lockups")
def ipos_lockups(
    window_days: int = Query(90, ge=7, le=365),
    top: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    return get_ipo_lockups(window_days=window_days, top=top)


@app.post("/signals/ipos/refresh")
def ipos_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int = Query(240, ge=1, le=365, description="Needs ≥180 days to populate lockup calendar."),
) -> dict[str, Any]:
    from providers.ipos import ensure_ipo_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=days)).isoformat()
    return ensure_ipo_window(start=start_d, end=end_d)


# ─── M&A / merger proxies ────────────────────────────────────────────────────

@app.get("/backtest/pbo")
def backtest_pbo(
    preset: str = Query(..., description="Preset to evaluate"),
    start: str = Query(...),
    end: str = Query(...),
    hold_days: str = Query("5,10,20,40", description="Comma-separated hold_days"),
    stop_loss_pct: str = Query("0,0.03,0.05,0.08"),
    profit_target_pct: str = Query("0,0.10,0.15,0.25"),
    position_mode: str = Query("fixed_usd"),
) -> dict[str, Any]:
    """Probability of Backtest Overfitting via CSCV on a hold×stop×target grid.

    Runs a lightweight sweep, aggregates daily returns per combo, then
    computes PBO: P(IS-best combo has below-median OOS rank).
    """
    from backtester.core import Backtest, _fetch_prices
    from backtester.signals import PRESETS
    from backtester.pbo import compute_pbo
    import datetime as _dt
    import itertools

    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        raise HTTPException(404, f"unknown preset {preset}")

    holds = [int(x) for x in hold_days.split(",") if x.strip()]
    stops = [float(x) for x in stop_loss_pct.split(",") if x.strip()]
    targets = [float(x) for x in profit_target_pct.split(",") if x.strip()]
    grid = list(itertools.product(holds, stops, targets))
    if len(grid) < 4:
        raise HTTPException(400, "grid too small for PBO (need ≥4 combos)")

    signals = preset_cfg["fn"](start=start, end=end)
    if not signals:
        raise HTTPException(400, "preset produced zero signals")

    pad = 60
    sd = (_dt.date.fromisoformat(start) - _dt.timedelta(days=pad)).isoformat()
    ed = (_dt.date.fromisoformat(end) + _dt.timedelta(days=pad)).isoformat()
    tickers = {s.ticker for s in signals} | {"SPY"}
    prices = _fetch_prices(tickers, sd, ed)

    # Run each combo, extract daily returns from equity curve
    all_returns: list[list[float]] = []  # [days][combos]
    labels: list[str] = []
    date_union: list | None = None
    combo_series: list[tuple[list[str], list[float]]] = []

    for (h, stp, tgt) in grid:
        bt = Backtest(
            signals=signals, start=start, end=end, hold_days=h,
            stop_loss_pct=stp or None, profit_target_pct=tgt or None,
            position_mode=position_mode if position_mode in ("fixed_usd", "pct_portfolio") else "fixed_usd",
            benchmark=None, prefetched_prices=prices,
        )
        r = bt.run()
        eq = r.equity_curve
        if eq.empty:
            continue
        dates = [str(d)[:10] for d in eq["date"].tolist()]
        equity = eq["equity"].astype(float).tolist()
        if len(equity) < 20:
            continue
        rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
        combo_series.append((dates[1:], rets))
        labels.append(f"h={h},stop={stp},tgt={tgt}")

    if len(combo_series) < 4:
        raise HTTPException(400, "not enough successful combos for PBO")

    # Align to common dates
    common = set(combo_series[0][0])
    for cs, _ in combo_series[1:]:
        common &= set(cs)
    common_sorted = sorted(common)
    if len(common_sorted) < 60:
        raise HTTPException(400, f"only {len(common_sorted)} common days — need ≥60")

    # Build matrix[day][combo]
    matrix: list[list[float]] = []
    # per-combo dict for O(1) lookup
    by_combo: list[dict[str, float]] = [dict(zip(ds, rs)) for (ds, rs) in combo_series]
    for d in common_sorted:
        row = [bc.get(d, 0.0) for bc in by_combo]
        matrix.append(row)

    pbo = compute_pbo(matrix, n_groups=min(16, len(common_sorted) // 10))
    pbo["combos_tested"] = labels
    pbo["common_days"] = len(common_sorted)
    return pbo


@app.get("/signals/mergers")
def mergers_list(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(500, ge=10, le=2000),
) -> dict[str, Any]:
    return get_merger_filings(start=start, end=end, top=top)


@app.post("/signals/mergers/refresh")
def mergers_refresh(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int = Query(120, ge=1, le=365),
) -> dict[str, Any]:
    from providers.mergers import ensure_merger_window
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=days)).isoformat()
    return ensure_merger_window(start=start_d, end=end_d)


# ─── Live IEX quotes via Alpaca ──────────────────────────────────────────────

@app.get("/live/enabled")
def live_enabled() -> dict[str, Any]:
    return {"enabled": alpaca_live.enabled()}


@app.get("/live/{ticker}")
def live_ticker(ticker: str) -> dict[str, Any]:
    """Single-ticker live snapshot: quote + trade + day bar + % change."""
    return alpaca_live.get_snapshot(ticker)


@app.get("/live")
def live_batch(
    tickers: str = Query(..., description="Comma-separated ticker list (max 200)"),
) -> dict[str, Any]:
    """Batch live snapshots. Returns {ticker: {bid, ask, last, day_change_pct, ...}}."""
    lst = [t.strip() for t in tickers.split(",") if t.strip()][:200]
    return alpaca_live.get_snapshots(lst)


@app.get("/paper")
def paper_list() -> dict[str, Any]:
    """List all paper portfolios with current equity + return."""
    return {"portfolios": paper.list_portfolios()}


@app.get("/paper/{portfolio_id}")
def paper_get(portfolio_id: int) -> dict[str, Any]:
    res = paper.get_portfolio(portfolio_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.post("/paper/create")
def paper_create(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Create a new paper portfolio.

    Body: {name, preset, config: {...all backtest params...},
           starting_cash, started_at?}
    """
    try:
        name = body["name"]
        preset = body["preset"]
        config = body.get("config") or {}
        starting_cash = float(body.get("starting_cash") or 100_000.0)
        started_at = body.get("started_at")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"missing field: {e}")
    try:
        result = paper.create_portfolio(
            name=name, preset=preset, config=config,
            starting_cash=starting_cash, started_at=started_at,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/paper/{portfolio_id}/advance")
def paper_advance(
    portfolio_id: int,
    as_of: str | None = Query(None, description="Roll forward to this ISO date; default today."),
) -> dict[str, Any]:
    """Advance a single portfolio to the given date (default today)."""
    return paper.advance_portfolio(portfolio_id, as_of=as_of)


@app.post("/paper/advance-all")
def paper_advance_all(
    as_of: str | None = Query(None),
) -> dict[str, Any]:
    """Roll every active portfolio forward."""
    return {"results": paper.advance_all(as_of=as_of)}


@app.delete("/paper/{portfolio_id}")
def paper_delete(portfolio_id: int) -> dict[str, Any]:
    return paper.delete_portfolio(portfolio_id)


@app.get("/signals/13f/funds")
def signals_13f_funds() -> dict[str, Any]:
    seed_fund_meta()
    return {"funds": list_funds()}


@app.get("/signals/13f/{cik}")
def signals_13f_fund(cik: int, top: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return get_fund_latest_holdings(cik, top=top)


@app.post("/signals/13f/refresh")
def signals_13f_refresh(max_filings: int = Query(6, ge=1, le=20)) -> dict[str, Any]:
    return ingest_all_funds(max_filings=max_filings)


@app.get("/signals/delistings")
def signals_delistings(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(60, ge=1, le=180),
) -> dict[str, Any]:
    """Form 15 + Form 25 — delistings and suspension of reporting."""
    start = start or (date.today() - timedelta(days=60)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_delistings_window(start, end, max_days=max_backfill_days)
    result = get_delistings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/foreign-issuers")
def signals_foreign_issuers(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(14, ge=1, le=60),
) -> dict[str, Any]:
    """20-F, 6-K, NT 20-F — foreign private issuer filings (ADRs)."""
    start = start or (date.today() - timedelta(days=7)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_foreign_filings_window(start, end, max_days=max_backfill_days)
    result = get_foreign_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/13g")
def signals_13g(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(45, ge=1, le=180),
) -> dict[str, Any]:
    """Schedule 13G — passive 5%+ ownership filings."""
    start = start or (date.today() - timedelta(days=60)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_13g_window(start, end, max_days=max_backfill_days)
    result = get_13g_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/form3")
def signals_form3(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(30, ge=1, le=180),
) -> dict[str, Any]:
    """Form 3 — initial statement of beneficial ownership (new insider arriving)."""
    start = start or (date.today() - timedelta(days=30)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_form3_window(start, end, max_days=max_backfill_days)
    result = get_form3_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/form-d")
def signals_form_d(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(30, ge=1, le=180),
) -> dict[str, Any]:
    """Form D — private placement fundraising notices (microcap pulse)."""
    start = start or (date.today() - timedelta(days=14)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_formD_window(start, end, max_days=max_backfill_days)
    result = get_formD_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/secondaries")
def signals_secondaries(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(45, ge=1, le=180),
) -> dict[str, Any]:
    """S-3 / 424B secondary offering filings — dilution events."""
    start = start or (date.today() - timedelta(days=60)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_secondaries_window(start, end, max_days=max_backfill_days)
    result = get_secondaries(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/tender-offers")
def signals_tender_offers(
    start: str | None = Query(None),
    end: str | None = Query(None),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(60, ge=1, le=180),
) -> dict[str, Any]:
    """SC TO-T / TO-I / TO-C / 14D9 — tender offer filings."""
    start = start or (date.today() - timedelta(days=90)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_tender_offers_window(start, end, max_days=max_backfill_days)
    result = get_tender_offers(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(f"Backfilled {len(coverage['ingested_days'])} days; {coverage['pending_days']} pending.")
    return {"range": {"start": start, "end": end}, "coverage": coverage, "warnings": warnings, **result}


@app.get("/signals/proxy-contests")
def signals_proxy_contests(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(45, ge=1, le=180),
) -> dict[str, Any]:
    """Activist proxy contest filings — DFAN14A / DEFC14A / PRRN14A / DFRN14A."""
    start = start or (date.today() - timedelta(days=90)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_proxy_contests_window(start, end, max_days=max_backfill_days)
    result = get_proxy_contests(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(
            f"Backfilled {len(coverage['ingested_days'])} days; "
            f"{coverage['pending_days']} still pending. Re-run to fetch more."
        )
    return {
        "range": {"start": start, "end": end},
        "coverage": coverage,
        "warnings": warnings,
        **result,
    }


@app.get("/signals/late-filings")
def signals_late_filings(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    top: int = Query(300, ge=1, le=1000),
    max_backfill_days: int = Query(60, ge=1, le=180),
) -> dict[str, Any]:
    """NT 10-K / NT 10-Q filings — companies that couldn't file on time."""
    start = start or (date.today() - timedelta(days=90)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_late_filings_window(start, end, max_days=max_backfill_days)
    result = get_late_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(
            f"Backfilled {len(coverage['ingested_days'])} days; "
            f"{coverage['pending_days']} still pending. Re-run to fetch more."
        )
    return {
        "range": {"start": start, "end": end},
        "coverage": coverage,
        "warnings": warnings,
        **result,
    }


@app.get("/signals/8k/items")
def signals_8k_items() -> dict[str, Any]:
    """List of 8-K item codes with friendly labels + default-interesting set."""
    return {
        "items": [
            {"code": c, "label": l, "default_on": c in FORM_8K_DEFAULT}
            for c, l in FORM_8K_ITEM_LABELS.items()
        ]
    }


@app.get("/signals/8k")
def signals_8k(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    items: str = Query("", description="comma-separated item codes; empty = all"),
    top: int = Query(200, ge=1, le=1000),
    max_backfill_days: int = Query(14, ge=1, le=60),
) -> dict[str, Any]:
    """8-K material event filings filtered by item codes."""
    start = start or (date.today() - timedelta(days=7)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_8k_window(start, end, max_days=max_backfill_days)
    items_list = [s.strip() for s in items.split(",") if s.strip()] or None
    result = get_8k_events(start, end, items_filter=items_list, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(
            f"Backfilled {len(coverage['ingested_days'])} days; "
            f"{coverage['pending_days']} days still pending. Re-run to fetch more."
        )
    return {
        "range": {"start": start, "end": end},
        "items_filter": items_list,
        "coverage": coverage,
        "warnings": warnings,
        **result,
    }


@app.get("/signals/activism")
def signals_activism(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    top: int = Query(100, ge=1, le=500),
    max_backfill_days: int = Query(30, ge=1, le=120),
) -> dict[str, Any]:
    """Schedule 13D / 13D-A activism filings."""
    start = start or (date.today() - timedelta(days=30)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_thirteen_d_window(start, end, max_days=max_backfill_days)
    result = get_activism_filings(start, end, top=top)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(
            f"Backfilled {len(coverage['ingested_days'])} days; "
            f"{coverage['pending_days']} days still pending. Re-run to fetch more."
        )
    return {
        "range": {"start": start, "end": end},
        "coverage": coverage,
        "warnings": warnings,
        **result,
    }


@app.get("/signals/planned-sells")
def planned_sells(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    top: int = Query(25, ge=1, le=500),
    min_value: float = Query(0, ge=0),
    max_backfill_days: int = Query(14, ge=1, le=60),
) -> dict[str, Any]:
    """Form 144 — planned insider sales. Live-scraped, throttled, day-level cached."""
    start = start or (date.today() - timedelta(days=7)).isoformat()
    end = end or date.today().isoformat()
    coverage = ensure_form144_window(start, end, max_days=max_backfill_days)
    result = get_planned_sells(start, end, top=top, min_value=min_value)
    warnings: list[str] = []
    if coverage["pending_days"] > 0:
        warnings.append(
            f"Backfilled {len(coverage['ingested_days'])} days; "
            f"{coverage['pending_days']} days still pending. "
            f"Re-run the query to fetch more."
        )
    return {
        "range": {"start": start, "end": end},
        "coverage": coverage,
        "warnings": warnings,
        "summary": result["summary"],
        "leaderboard": result["leaderboard"],
        "transactions": result["transactions"],
    }


@app.get("/fundamentals/{ticker}")
def ticker_fundamentals(
    ticker: str,
    years: int = Query(5, ge=1, le=20),
    ingest_years: int = Query(3, ge=1, le=20, description="backfill FSDS this many years"),
) -> dict[str, Any]:
    """XBRL fundamentals for a ticker (IS / BS / CF for the last N 10-K filings).

    On first call for a fresh DB, ingests the last `ingest_years` of FSDS quarters.
    """
    today = date.today()
    start = today.replace(year=today.year - ingest_years).isoformat()
    end = today.isoformat()
    ensure_fundamentals_quarters(start, end)
    return get_fundamentals(ticker, years=years)


@app.get("/screener")
def screener(
    order_by: str = Query("market_cap"),
    descending: bool = Query(True),
    limit: int = Query(200, ge=1, le=2000),
    # Each ratio gets an optional _min and _max; only a handful are wired from the UI.
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    pe_ratio_min: float | None = None,
    pe_ratio_max: float | None = None,
    pb_ratio_max: float | None = None,
    ps_ratio_max: float | None = None,
    ev_ebitda_max: float | None = None,
    fcf_yield_min: float | None = None,
    earnings_yield_min: float | None = None,
    gross_margin_min: float | None = None,
    operating_margin_min: float | None = None,
    net_margin_min: float | None = None,
    roe_min: float | None = None,
    roa_min: float | None = None,
    debt_to_equity_max: float | None = None,
    revenue_growth_yoy_min: float | None = None,
    net_income_growth_yoy_min: float | None = None,
    quality_score_min: float | None = None,
    magic_rank_max: float | None = None,
    exclude_etfs: bool = Query(False, description="Filter out ETFs / trusts / funds by name."),
) -> dict[str, Any]:
    filters = {k: v for k, v in locals().items() if k.endswith(("_min", "_max")) and v is not None}
    if exclude_etfs:
        filters["exclude_etfs"] = True
    return screener_query(
        filters=filters, order_by=order_by, descending=descending, limit=limit
    )


@app.post("/screener/refresh")
def screener_refresh(max_tickers: int | None = None) -> dict[str, Any]:
    return compute_ratios_snapshot(max_tickers=max_tickers)


@app.get("/fundamentals/{ticker}/ratios")
def ticker_ratios(ticker: str) -> dict[str, Any]:
    return get_key_ratios(ticker)


@app.post("/fundamentals/refresh-ticker-map")
def refresh_tickers() -> dict[str, Any]:
    n = refresh_ticker_map()
    return {"rows": n}


@app.get("/tickers/search")
def tickers_search(
    q: str = Query("", description="Ticker prefix or name substring"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return {"query": q, "results": search_tickers(q, limit=limit)}


@app.get("/tickers/{ticker}/cik")
def ticker_cik(ticker: str) -> dict[str, Any]:
    cik = ticker_to_cik(ticker)
    return {"ticker": ticker.upper(), "cik": cik}


@app.get("/prices/{ticker}")
def ticker_prices(
    ticker: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """EOD OHLCV for a ticker + issuer metadata. Powered by yfinance (disk-cached)."""
    try:
        bars = prices.get_history(ticker, start, end)
    except prices.RateLimited as e:
        raise HTTPException(
            status_code=503,
            detail=f"Yahoo Finance is rate-limiting us. Try again in a few minutes. ({e})",
        )
    info = prices.get_info(ticker)
    return {
        "ticker": ticker.upper(),
        "range": {"start": start, "end": end},
        "info": info,
        "bars": bars,
    }


@app.get("/tickers/{ticker}/dossier")
def ticker_dossier(
    ticker: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    top_per_type: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    """All SEC signals for a ticker in one query. Pulls Form 4 buys, Form 144
    sells, 13D/13G filings, 8-K items, NT filings, proxy contests, secondaries,
    tender offers, Form 3, and superinvestor holders."""
    return get_ticker_dossier(ticker, start, end, top_per_type=top_per_type)


@app.get("/tickers/{ticker}/form144")
def ticker_form144(
    ticker: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """Form 144 planned sales for a single ticker in a window."""
    cik = ticker_to_cik(ticker)
    con = connect()
    rows: list[tuple[Any, ...]] = []
    if cik is not None:
        rows = con.execute(
            """
            SELECT filing_date, approx_sale_date, seller_name, relationship,
                   class_title, shares, aggregate_value, broker_name, accession
            FROM form144
            WHERE issuer_cik = ?
              AND filing_date BETWEEN ? AND ?
              AND shares IS NOT NULL AND shares > 0
              AND aggregate_value IS NOT NULL AND aggregate_value > 0
            ORDER BY filing_date ASC, aggregate_value DESC
            """,
            [cik, start, end],
        ).fetchall()
    con.close()
    cols = [
        "filing_date", "approx_sale_date", "seller_name", "relationship",
        "class_title", "shares", "aggregate_value", "broker_name", "accession",
    ]
    out = [
        {c: (v.isoformat() if isinstance(v, date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    return {"ticker": ticker.upper(), "range": {"start": start, "end": end}, "sells": out}


@app.get("/tickers/{ticker}/insider-buys")
def ticker_insider_buys(
    ticker: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """Insider purchases for a single ticker in a window — used by the impact chart."""
    ensure_insider_quarters(start, end)
    con = connect()
    rows = con.execute(
        """
        SELECT tx.TRANS_DATE AS date,
               sub.ISSUERNAME AS issuer,
               tx.TRANS_SHARES AS shares,
               tx.TRANS_PRICEPERSHARE AS price,
               tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE AS value,
               tx.ACCESSION_NUMBER AS accession,
               own.RPTOWNERNAME AS insider_name,
               COALESCE(own.RPTOWNER_TITLE, own.RPTOWNER_RELATIONSHIP, '') AS position
        FROM insider_nonderiv_trans tx
        JOIN insider_submission sub USING (ACCESSION_NUMBER)
        LEFT JOIN insider_reporting_owner own USING (ACCESSION_NUMBER)
        WHERE tx.TRANS_CODE = 'P'
          AND tx.TRANS_DATE BETWEEN ? AND ?
          AND UPPER(sub.ISSUERTRADINGSYMBOL) = UPPER(?)
          AND tx.TRANS_SHARES > 0
          AND tx.TRANS_PRICEPERSHARE BETWEEN 0.01 AND 10000
        ORDER BY tx.TRANS_DATE ASC, value DESC
        """,
        [start, end, ticker],
    ).fetchall()
    cols = ["date", "issuer", "shares", "price", "value", "accession", "insider_name", "position"]
    out = [
        {c: (v.isoformat() if isinstance(v, date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    con.close()
    return {"ticker": ticker.upper(), "range": {"start": start, "end": end}, "buys": out}


@app.get("/signals/insider-buying/live")
def insider_buying_live(
    symbol: str = Query(..., description="Ticker symbol"),
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """Single-ticker live lookup via Finnhub (bridges the current-quarter gap)."""
    if not finnhub.enabled():
        return {"error": "FINNHUB_API_KEY not set", "transactions": []}
    start = start or _bdays_ago(30).isoformat()
    end = end or date.today().isoformat()
    txns = finnhub.get_insider_transactions(symbol, start, end)
    return {
        "symbol": symbol,
        "range": {"start": start, "end": end},
        "source": "finnhub",
        "transactions": txns,
    }

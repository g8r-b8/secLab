"""Forward-test / paper-trading harness.

Takes a preset + sizing config (same surface as the backtester) and runs it
live on fresh DERA quarterly data. Positions are stored in DuckDB so state
persists across restarts. Designed to be called once per day — pulls any new
signals since last_updated, opens/exits trades per the config, and snapshots
the equity curve.

No real broker integration — this is a paper book that answers "what would
my strategy have done if I'd gone live on day X".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

import pandas as pd

from backtester.core import Backtest, Signal, _fetch_prices
from backtester.signals import PRESETS
from data_store import connect

log = logging.getLogger("seclab")


# ─── Portfolio CRUD ─────────────────────────────────────────────────────────

def create_portfolio(
    name: str,
    preset: str,
    config: dict[str, Any],
    starting_cash: float = 100_000.0,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Register a new paper portfolio. Doesn't advance it — call advance() after."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset: {preset}")
    start = started_at or dt.date.today().isoformat()
    con = connect()
    try:
        con.execute(
            """
            INSERT INTO paper_portfolio
                (name, preset, config_json, started_at, starting_cash, status, last_updated)
            VALUES (?, ?, ?, ?, ?, 'active', NULL)
            """,
            [name, preset, json.dumps(config), start, float(starting_cash)],
        )
        row = con.execute(
            "SELECT id FROM paper_portfolio WHERE name = ?", [name]
        ).fetchone()
        if not row:
            raise RuntimeError("insert succeeded but id lookup failed")
        pid = int(row[0])
        # Seed the equity curve at started_at = cash only, no positions
        con.execute(
            """
            INSERT INTO paper_equity VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [pid, start, float(starting_cash), float(starting_cash), 0.0],
        )
    finally:
        con.close()
    return get_portfolio(pid)


def list_portfolios() -> list[dict[str, Any]]:
    con = connect()
    try:
        rows = con.execute(
            """
            SELECT p.id, p.name, p.preset, p.started_at, p.starting_cash,
                   p.status, p.last_updated,
                   (SELECT equity FROM paper_equity WHERE portfolio_id = p.id
                    ORDER BY date DESC LIMIT 1) AS current_equity,
                   (SELECT COUNT(*) FROM paper_trade WHERE portfolio_id = p.id
                    AND exit_date IS NULL) AS n_open,
                   (SELECT COUNT(*) FROM paper_trade WHERE portfolio_id = p.id) AS n_trades
            FROM paper_portfolio p
            ORDER BY p.id DESC
            """
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "id": int(r[0]),
            "name": r[1],
            "preset": r[2],
            "started_at": r[3].isoformat() if r[3] else None,
            "starting_cash": float(r[4]),
            "status": r[5],
            "last_updated": r[6].isoformat() if r[6] else None,
            "current_equity": float(r[7]) if r[7] is not None else float(r[4]),
            "total_return_pct": (float(r[7]) / float(r[4]) - 1) if r[7] else 0.0,
            "n_open_positions": int(r[8] or 0),
            "n_trades": int(r[9] or 0),
        }
        for r in rows
    ]


def get_portfolio(portfolio_id: int, trade_limit: int = 500) -> dict[str, Any]:
    con = connect()
    try:
        hdr = con.execute(
            """
            SELECT id, name, preset, config_json, started_at, starting_cash,
                   status, last_updated
            FROM paper_portfolio WHERE id = ?
            """,
            [portfolio_id],
        ).fetchone()
        if not hdr:
            return {"error": f"portfolio {portfolio_id} not found"}
        equity = con.execute(
            """
            SELECT date, equity, cash, invested
            FROM paper_equity WHERE portfolio_id = ? ORDER BY date
            """,
            [portfolio_id],
        ).fetchall()
        trades = con.execute(
            f"""
            SELECT ticker, signal_date, entry_date, entry_price, shares,
                   position_value, exit_date, exit_price, exit_reason,
                   pnl, return_pct, label, strength
            FROM paper_trade WHERE portfolio_id = ?
            ORDER BY entry_date DESC, trade_id DESC
            LIMIT {int(trade_limit)}
            """,
            [portfolio_id],
        ).fetchall()
    finally:
        con.close()
    cfg = json.loads(hdr[3]) if hdr[3] else {}
    return {
        "id": int(hdr[0]),
        "name": hdr[1],
        "preset": hdr[2],
        "config": cfg,
        "started_at": hdr[4].isoformat() if hdr[4] else None,
        "starting_cash": float(hdr[5]),
        "status": hdr[6],
        "last_updated": hdr[7].isoformat() if hdr[7] else None,
        "equity_curve": [
            {
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
                "equity": float(eq),
                "cash": float(cash),
                "invested": float(inv),
            }
            for (d, eq, cash, inv) in equity
        ],
        "trades": [
            {
                "ticker": t[0],
                "signal_date": t[1].isoformat() if t[1] else None,
                "entry_date": t[2].isoformat() if t[2] else None,
                "entry_price": float(t[3]) if t[3] is not None else None,
                "shares": float(t[4]) if t[4] is not None else None,
                "position_value": float(t[5]) if t[5] is not None else None,
                "exit_date": t[6].isoformat() if t[6] else None,
                "exit_price": float(t[7]) if t[7] is not None else None,
                "exit_reason": t[8],
                "pnl": float(t[9]) if t[9] is not None else None,
                "return_pct": float(t[10]) if t[10] is not None else None,
                "label": t[11] or "",
                "strength": float(t[12]) if t[12] is not None else 1.0,
            }
            for t in trades
        ],
    }


def delete_portfolio(portfolio_id: int) -> dict[str, Any]:
    con = connect()
    try:
        con.execute("DELETE FROM paper_equity WHERE portfolio_id = ?", [portfolio_id])
        con.execute("DELETE FROM paper_trade WHERE portfolio_id = ?", [portfolio_id])
        con.execute("DELETE FROM paper_portfolio WHERE id = ?", [portfolio_id])
    finally:
        con.close()
    return {"ok": True, "deleted_id": portfolio_id}


# ─── Advance engine ─────────────────────────────────────────────────────────

def advance_portfolio(portfolio_id: int, as_of: str | None = None) -> dict[str, Any]:
    """Roll this portfolio forward from last_updated → as_of (default: today).

    Strategy: re-run the backtester on the window [started_at, as_of] using the
    stored config, then DIFF with the stored trades/equity and insert any new
    rows. This keeps the paper run idempotent — calling it twice on the same
    date is a no-op.
    """
    end = as_of or dt.date.today().isoformat()
    p = get_portfolio(portfolio_id)
    if "error" in p:
        return p
    preset = p["preset"]
    cfg = p.get("config") or {}
    start = p["started_at"]
    starting_cash = p["starting_cash"]

    preset_cfg = PRESETS.get(preset)
    if not preset_cfg:
        return {"error": f"unknown preset {preset}"}

    log.info(f"[paper] advancing '{p['name']}' ({preset}) {start} → {end}")
    signals = preset_cfg["fn"](start=start, end=end)
    if not signals:
        _record_empty_snapshot(portfolio_id, end, starting_cash)
        return get_portfolio(portfolio_id)

    bt = Backtest(
        signals=signals,
        start=start,
        end=end,
        hold_days=int(cfg.get("hold_days", 10)),
        position_mode=cfg.get("position_mode", "fixed_usd"),
        position_size=float(cfg.get("position_size", 10_000)),
        position_pct=float(cfg.get("position_pct", 0.02)),
        risk_per_trade_pct=float(cfg.get("risk_per_trade_pct", 0.01)),
        target_vol_pct=float(cfg.get("target_vol_pct", 0.10)),
        target_deployment_pct=float(cfg.get("target_deployment_pct", 0.80)),
        strength_scale_cap=float(cfg.get("strength_scale_cap", 3.0)),
        stop_loss_pct=(float(cfg.get("stop_loss_pct", 0)) or None),
        profit_target_pct=(float(cfg.get("profit_target_pct", 0)) or None),
        trailing_stop_pct=(float(cfg.get("trailing_stop_pct", 0)) or None),
        slippage_bps=float(cfg.get("slippage_bps", 0)),
        commission_per_trade=float(cfg.get("commission_per_trade", 0)),
        one_position_per_ticker=bool(cfg.get("one_position_per_ticker", False)),
        max_signals_per_day=(int(cfg.get("max_signals_per_day") or 0) or None),
        max_sector_pct=(float(cfg.get("max_sector_pct") or 0) or None),
        correlation_adjust=bool(cfg.get("correlation_adjust", False)),
        correlation_shrinkage=float(cfg.get("correlation_shrinkage", 0.5)),
        correlation_lookback=int(cfg.get("correlation_lookback", 60)),
        starting_cash=starting_cash,
        benchmark=cfg.get("benchmark") or None,
    )
    result = bt.run()
    _persist_run(portfolio_id, result, end)
    return get_portfolio(portfolio_id)


def _persist_run(portfolio_id: int, result, as_of: str) -> None:
    """Replace stored trades + equity for this portfolio with the fresh run."""
    con = connect()
    try:
        # Wipe and re-insert — simple and safe for a daily refresh on a
        # per-portfolio basis. Trades are deterministic given the config.
        con.execute("DELETE FROM paper_trade WHERE portfolio_id = ?", [portfolio_id])
        con.execute("DELETE FROM paper_equity WHERE portfolio_id = ?", [portfolio_id])

        rows_t = []
        for i, t in enumerate(result.trades):
            exit_date = t.exit_date if t.exit_date and t.exit_date <= as_of else None
            exit_price = t.exit_price if exit_date else None
            exit_reason = t.exit_reason if exit_date else None
            pnl = t.pnl if exit_date else None
            ret = t.return_pct if exit_date else None
            rows_t.append((
                portfolio_id, i,
                t.ticker, t.signal_date, t.entry_date, float(t.entry_price),
                float(t.shares), float(t.position_value),
                exit_date, exit_price, exit_reason, pnl, ret,
                t.label or "", float(getattr(t, "strength", 1.0)),
            ))
        if rows_t:
            con.executemany(
                """
                INSERT INTO paper_trade
                    (portfolio_id, trade_id, ticker, signal_date, entry_date,
                     entry_price, shares, position_value, exit_date, exit_price,
                     exit_reason, pnl, return_pct, label, strength)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_t,
            )

        eq = result.equity_curve
        rows_e = []
        for _, r in eq.iterrows():
            d = r["date"]
            rows_e.append((
                portfolio_id,
                d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
                float(r["equity"]), float(r["cash"]), float(r["invested"]),
            ))
        if rows_e:
            con.executemany(
                "INSERT INTO paper_equity VALUES (?, ?, ?, ?, ?)",
                rows_e,
            )
        con.execute(
            "UPDATE paper_portfolio SET last_updated = ? WHERE id = ?",
            [as_of, portfolio_id],
        )
    finally:
        con.close()


def _record_empty_snapshot(portfolio_id: int, as_of: str, starting_cash: float) -> None:
    """Snapshot equity = cash when no signals. Useful for fresh portfolios."""
    con = connect()
    try:
        con.execute(
            """
            INSERT INTO paper_equity VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [portfolio_id, as_of, float(starting_cash), float(starting_cash), 0.0],
        )
        con.execute(
            "UPDATE paper_portfolio SET last_updated = ? WHERE id = ?",
            [as_of, portfolio_id],
        )
    finally:
        con.close()


def advance_all(as_of: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in list_portfolios():
        if p["status"] != "active":
            continue
        try:
            r = advance_portfolio(p["id"], as_of=as_of)
            out.append({"id": p["id"], "name": p["name"], "status": "ok"})
        except Exception as e:
            log.warning(f"[paper] advance {p['id']} ({p['name']}) failed: {e}")
            out.append({"id": p["id"], "name": p["name"], "status": "error", "error": str(e)})
    return out

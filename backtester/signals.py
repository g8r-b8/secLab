"""Signal generators — produce lists of `Signal` from our DuckDB data."""
from __future__ import annotations

import datetime as dt

from backtester.core import Signal
from data_store import connect, ensure_insider_quarters


def insider_cluster_buys(
    start: str,
    end: str,
    min_insiders: int = 2,
    min_value: float = 50_000,
    require_csuite: bool = False,
) -> list[Signal]:
    """Form 4 open-market buys where ≥N distinct insiders bought the same ticker on the same day.

    Signal date = the day the cluster occurred. One signal per (ticker, day).
    """
    # Ensure the window's quarters are ingested — idempotent if already present.
    ensure_insider_quarters(start, end)

    con = connect()
    where_csuite = ""
    if require_csuite:
        where_csuite = """
          AND EXISTS (
            SELECT 1 FROM insider_reporting_owner own
            WHERE own.ACCESSION_NUMBER = tx.ACCESSION_NUMBER
              AND regexp_matches(UPPER(COALESCE(own.RPTOWNER_TITLE,'')),
                                 'CEO|CHIEF EXECUTIVE|CFO|CHIEF FINANCIAL|PRESIDENT')
          )
        """

    rows = con.execute(
        f"""
        SELECT tx.TRANS_DATE AS date,
               sub.ISSUERTRADINGSYMBOL AS ticker,
               COUNT(DISTINCT own.RPTOWNERCIK) AS insiders,
               SUM(tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE) AS total_value
        FROM insider_nonderiv_trans tx
        JOIN insider_submission sub USING (ACCESSION_NUMBER)
        LEFT JOIN insider_reporting_owner own USING (ACCESSION_NUMBER)
        WHERE tx.TRANS_CODE = 'P'
          AND tx.TRANS_DATE BETWEEN ? AND ?
          AND tx.TRANS_SHARES > 0
          AND tx.TRANS_PRICEPERSHARE BETWEEN 0.01 AND 10000
          AND sub.ISSUERTRADINGSYMBOL IS NOT NULL
          AND UPPER(sub.ISSUERTRADINGSYMBOL) NOT IN ('NONE','N/A','')
          {where_csuite}
        GROUP BY 1, 2
        HAVING COUNT(DISTINCT own.RPTOWNERCIK) >= ?
           AND SUM(tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE) >= ?
        ORDER BY date, ticker
        """,
        [start, end, min_insiders, min_value],
    ).fetchall()
    con.close()

    import math as _m
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=1,
            label=f"{ins} insiders, ${tv:,.0f}",
            # Strength: cluster size × log-dollar magnitude. A 5-insider $1M buy
            # scores much higher than a 2-insider $60k buy.
            strength=float(ins) * _m.log10(max(float(tv), 10_000)),
        )
        for d, t, ins, tv in rows
    ]


def csuite_insider_buys(
    start: str,
    end: str,
    min_value: float = 100_000,
) -> list[Signal]:
    """Any C-suite (CEO/CFO/President) open-market purchase over $N."""
    ensure_insider_quarters(start, end)
    con = connect()
    rows = con.execute(
        """
        SELECT tx.TRANS_DATE AS date,
               sub.ISSUERTRADINGSYMBOL AS ticker,
               STRING_AGG(DISTINCT COALESCE(own.RPTOWNER_TITLE,''), '; ') AS titles,
               SUM(tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE) AS total_value
        FROM insider_nonderiv_trans tx
        JOIN insider_submission sub USING (ACCESSION_NUMBER)
        JOIN insider_reporting_owner own USING (ACCESSION_NUMBER)
        WHERE tx.TRANS_CODE = 'P'
          AND tx.TRANS_DATE BETWEEN ? AND ?
          AND tx.TRANS_SHARES > 0
          AND tx.TRANS_PRICEPERSHARE BETWEEN 0.01 AND 10000
          AND sub.ISSUERTRADINGSYMBOL IS NOT NULL
          AND UPPER(sub.ISSUERTRADINGSYMBOL) NOT IN ('NONE','N/A','')
          AND regexp_matches(UPPER(COALESCE(own.RPTOWNER_TITLE,'')),
                             'CEO|CHIEF EXECUTIVE|CFO|CHIEF FINANCIAL|PRESIDENT')
        GROUP BY 1, 2
        HAVING SUM(tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE) >= ?
        ORDER BY date, ticker
        """,
        [start, end, min_value],
    ).fetchall()
    con.close()
    import math as _m
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=1,
            label=f"${tv:,.0f} C-suite",
            strength=_m.log10(max(float(tv), 10_000)),
        )
        for d, t, _, tv in rows
    ]


def form144_cluster_sells(
    start: str,
    end: str,
    min_sellers: int = 2,
    min_value: float = 100_000,
) -> list[Signal]:
    """Form 144 where ≥N distinct sellers file on the same ticker same day.

    direction=-1 (short signal). Note: Form 144 data has to have been scraped
    via /signals/planned-sells for the window first.
    """
    con = connect()
    rows = con.execute(
        """
        SELECT f.filing_date AS date,
               tm.ticker AS ticker,
               COUNT(DISTINCT f.seller_name) AS sellers,
               SUM(f.aggregate_value) AS total_value
        FROM form144 f
        JOIN ticker_map tm ON tm.cik = f.issuer_cik
        WHERE f.filing_date BETWEEN ? AND ?
          AND f.aggregate_value IS NOT NULL AND f.aggregate_value > 0
        GROUP BY 1, 2
        HAVING COUNT(DISTINCT f.seller_name) >= ?
           AND SUM(f.aggregate_value) >= ?
        ORDER BY date, ticker
        """,
        [start, end, min_sellers, min_value],
    ).fetchall()
    con.close()
    import datetime as dt
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=-1,
            label=f"{s} sellers, ${tv:,.0f}",
        )
        for d, t, s, tv in rows
    ]


def proxy_contests_long(start: str, end: str) -> list[Signal]:
    """Activist proxy-contest filings as a long signal on issuer_ticker.

    Targets of activist campaigns typically see positive abnormal returns on
    campaign announcement. Requires the window to have been pre-ingested via
    /signals/proxy-contests.
    """
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, form, filer_name
        FROM proxy_contests
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL AND issuer_ticker <> ''
        ORDER BY filing_date, issuer_ticker
        """,
        [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=1,
            label=f"{form} by {fn or '?'}",
        )
        for d, t, form, fn in rows
    ]


def schedule_13g_long(start: str, end: str) -> list[Signal]:
    """Passive 5%+ ownership filings — weaker signal than 13D but volume-based."""
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, form, filer_name FROM schedule_13g
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL AND issuer_ticker <> ''
          AND form = 'SCHEDULE 13G'
        ORDER BY filing_date, issuer_ticker
        """, [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(ticker=t, signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
               direction=1, label=f"{form} by {fn or '?'}")
        for d, t, form, fn in rows
    ]


def delistings_short(start: str, end: str) -> list[Signal]:
    """Form 15/25 delistings — short/avoid signal (often fires on already-distressed names)."""
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, form FROM delistings
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL AND issuer_ticker <> ''
        ORDER BY filing_date, issuer_ticker
        """, [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(ticker=t, signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
               direction=-1, label=form)
        for d, t, form in rows
    ]


def form3_long(start: str, end: str) -> list[Signal]:
    """New insider onboarding — long signal on issuer_ticker (weaker; informational)."""
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, filer_name FROM form3_initials
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL AND issuer_ticker <> ''
        ORDER BY filing_date, issuer_ticker
        """, [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(ticker=t, signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
               direction=1, label=f"Form 3 · {fn or '?'}")
        for d, t, fn in rows
    ]


def secondaries_short(start: str, end: str) -> list[Signal]:
    """S-3/424B dilution events — short signal on issuer_ticker."""
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, form FROM secondaries
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL AND issuer_ticker <> ''
        ORDER BY filing_date, issuer_ticker
        """,
        [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(ticker=t,
               signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
               direction=-1, label=form)
        for d, t, form in rows
    ]


def tender_offers_long(start: str, end: str) -> list[Signal]:
    """Tender-offer targets — long signal on subject_ticker. Typically a jump to offer price."""
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, subject_ticker, form, filer_name FROM tender_offers
        WHERE filing_date BETWEEN ? AND ?
          AND subject_ticker IS NOT NULL AND subject_ticker <> ''
          AND form IN ('SC TO-T', 'SC 14D9')
        ORDER BY filing_date, subject_ticker
        """,
        [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(ticker=t,
               signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
               direction=1, label=f"{form} by {fn or '?'}")
        for d, t, form, fn in rows
    ]


def late_filings_short(start: str, end: str) -> list[Signal]:
    """NT 10-K / NT 10-Q filings → short signal on issuer_ticker.

    Academic evidence: these precede negative news (~60% of the time).
    Requires the window to have been pre-ingested via /signals/late-filings.
    """
    import datetime as dt
    from data_store import connect
    con = connect()
    rows = con.execute(
        """
        SELECT filing_date, issuer_ticker, form
        FROM late_filings
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL
          AND issuer_ticker <> ''
        ORDER BY filing_date, issuer_ticker
        """,
        [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=-1,
            label=form,
        )
        for d, t, form in rows
    ]


def schedule_13d_filings(start: str, end: str, include_amendments: bool = False) -> list[Signal]:
    """Every Schedule 13D (and optionally 13D/A) → long signal on issuer_ticker.

    Requires the window to have been pre-ingested via /signals/activism. Issuers
    with no ticker (private entities) are skipped since we can't trade them.
    """
    import datetime as dt
    from data_store import connect
    con = connect()
    form_filter = "" if include_amendments else "AND form = 'SCHEDULE 13D'"
    rows = con.execute(
        f"""
        SELECT filing_date, issuer_ticker, filer_name
        FROM thirteen_d
        WHERE filing_date BETWEEN ? AND ?
          AND issuer_ticker IS NOT NULL
          AND issuer_ticker <> ''
          {form_filter}
        ORDER BY filing_date, issuer_ticker
        """,
        [start, end],
    ).fetchall()
    con.close()
    return [
        Signal(
            ticker=t,
            signal_date=d.isoformat() if isinstance(d, dt.date) else str(d)[:10],
            direction=1,
            label=f"13D by {fn or '?'}",
        )
        for d, t, fn in rows
    ]


def _intersect_signals(
    base: list[Signal], overlay: list[Signal], window_days: int
) -> list[Signal]:
    """Keep only `base` signals that have a matching `overlay` signal on the
    same ticker within ±window_days."""
    import datetime as dt
    by_ticker: dict[str, list[dt.date]] = {}
    for s in overlay:
        try:
            d = dt.date.fromisoformat(s.signal_date)
        except ValueError:
            continue
        by_ticker.setdefault(s.ticker.upper(), []).append(d)
    out: list[Signal] = []
    for s in base:
        try:
            d = dt.date.fromisoformat(s.signal_date)
        except ValueError:
            continue
        for o in by_ticker.get(s.ticker.upper(), ()):
            if abs((o - d).days) <= window_days:
                out.append(
                    Signal(
                        ticker=s.ticker,
                        signal_date=s.signal_date,
                        direction=s.direction,
                        label=f"{s.label} + overlay",
                    )
                )
                break
    return out


def cluster_buys_plus_13d(start: str, end: str) -> list[Signal]:
    """Cluster insider buys that coincide with a Schedule 13D filing on the
    same ticker within ±10 days. Extremely high-signal intersection."""
    base = insider_cluster_buys(start=start, end=end, min_insiders=2, min_value=50_000)
    overlay = schedule_13d_filings(start=start, end=end)
    return _intersect_signals(base, overlay, window_days=10)


def cluster_buys_plus_csuite(start: str, end: str) -> list[Signal]:
    """Cluster insider buys that also have C-suite participation (≥$50k either
    threshold). Signals where both cluster AND C-suite fire in the same window."""
    base = insider_cluster_buys(start=start, end=end, min_insiders=2, min_value=50_000)
    overlay = csuite_insider_buys(start=start, end=end, min_value=50_000)
    return _intersect_signals(base, overlay, window_days=3)


def csuite_plus_13d(start: str, end: str) -> list[Signal]:
    """C-suite insider buy AND 13D filer crossed 5% on same ticker within 10 days."""
    base = csuite_insider_buys(start=start, end=end, min_value=100_000)
    overlay = schedule_13d_filings(start=start, end=end)
    return _intersect_signals(base, overlay, window_days=10)


def auditor_changes_short(
    start: str,
    end: str,
) -> list[Signal]:
    """8-K Item 4.01 (changes in certifying accountant) — short signal.

    Classic red flag. Auditor changes often precede restatements, going-
    concern doubts, or fraud revelations. Emits a short signal on the filing
    date for any form_8k row whose `items` field contains '4.01'.

    NOTE: form_8k only stretches back to Dec 2025 in our DuckDB; backfill
    to 2018+ is a standing TODO. Until then, historical backtests use a
    short window but the live/paper feed works fine.
    """
    con = connect()
    try:
        rows = con.execute(
            """
            SELECT filing_date, issuer_ticker, issuer_name, items
            FROM form_8k
            WHERE items LIKE '%4.01%'
              AND filing_date BETWEEN ? AND ?
              AND issuer_ticker IS NOT NULL
              AND upper(issuer_ticker) NOT IN ('', 'N/A', 'NONE')
            ORDER BY filing_date, issuer_ticker
            """,
            [start, end],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return []
    out: list[Signal] = []
    seen: set[tuple[str, str]] = set()
    for d, ticker, name, items in rows:
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        key = (ticker.upper(), iso)
        if key in seen:
            continue
        seen.add(key)
        # Strength: small boost if the filing is *just* 4.01 (more "focused"
        # auditor-change event) vs bundled with many items.
        n_items = len([x for x in (items or "").split(",") if x.strip()])
        strength = 2.0 if n_items <= 2 else 1.0
        out.append(Signal(
            ticker=ticker.upper(),
            signal_date=iso,
            direction=-1,
            label=f"8-K items: {items}",
            strength=strength,
        ))
    return out


def piotroski_long(
    start: str,
    end: str,
    min_quality: int = 4,
    top_n: int = 30,
    market_cap_min: float = 1_000_000_000,
    rebalance_months: int = 3,
) -> list[Signal]:
    """Quarterly rebalance into top-quality names by reduced Piotroski F-score.

    For each rebalance date in [start, end] (every `rebalance_months` starting
    at the end of each quarter), pick the top-N names ranked by magic_rank
    ASCENDING among those with quality_score >= `min_quality` and market cap
    >= `market_cap_min`. Emit one signal per (ticker, rebalance_date).

    IMPORTANT — LOOKAHEAD CAVEAT: ratios_snapshot is a single point-in-time
    table (current filings as of the last refresh). When this preset runs
    over a historical window, it's using TODAY's fundamentals for every past
    rebalance — i.e. any stock that's gone bankrupt since won't appear, and
    revenue-growth flags use post-hoc data. For honest backtesting we need
    point-in-time ratios per quarter (Bundle C TODO). This preset is useful
    as a forward/paper-trade driver today; treat historical Sharpes with
    skepticism until we build the asof layer.
    """
    import math as _m
    con = connect()
    try:
        rows = con.execute(
            """
            WITH scored AS (
                SELECT
                    ticker, name, market_cap, earnings_yield, roa,
                    (
                        CAST(COALESCE(net_income, 0) > 0 AS INTEGER) +
                        CAST(COALESCE(roa, 0) > 0 AS INTEGER) +
                        CAST(COALESCE(revenue_growth_yoy, 0) > 0 AS INTEGER) +
                        CAST(COALESCE(net_income_growth_yoy, 0) > 0 AS INTEGER) +
                        CAST(COALESCE(debt_to_equity, 999) < 1 AS INTEGER)
                    ) AS quality_score,
                    (
                        RANK() OVER (ORDER BY earnings_yield DESC NULLS LAST) +
                        RANK() OVER (ORDER BY roa DESC NULLS LAST)
                    ) AS magic_rank
                FROM ratios_snapshot
                WHERE COALESCE(market_cap, 0) >= ?
                  AND upper(COALESCE(name, '')) NOT LIKE '%TRUST%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%ETF%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%FUND%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%PROSHARES%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%ISHARES%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%SPDR%'
                  AND upper(COALESCE(name, '')) NOT LIKE '%ABRDN%'
            )
            SELECT ticker, name, quality_score, magic_rank, earnings_yield, roa
            FROM scored
            WHERE quality_score >= ?
            ORDER BY magic_rank ASC
            LIMIT ?
            """,
            [float(market_cap_min), int(min_quality), int(top_n)],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return []

    # Rebalance dates: every rebalance_months months from start to end,
    # anchored to end-of-quarter dates.
    import datetime as _dt
    sd = _dt.date.fromisoformat(start)
    ed = _dt.date.fromisoformat(end)
    rebalance_dates: list[_dt.date] = []
    year, month = sd.year, sd.month
    # Bump to the next rebalance boundary
    while True:
        # End of month: day 28 is always safe; we care about the calendar
        # month, not the literal last trading day.
        d = _dt.date(year, month, 28)
        if d >= sd:
            rebalance_dates.append(d)
        if d > ed:
            break
        month += int(rebalance_months)
        while month > 12:
            month -= 12
            year += 1
    # Trim any past end
    rebalance_dates = [d for d in rebalance_dates if sd <= d <= ed]
    if not rebalance_dates:
        return []

    signals: list[Signal] = []
    for d in rebalance_dates:
        for (ticker, name, q, mf, ey, roa) in rows:
            # Strength: lower magic_rank ⇒ higher strength. Also bonus for
            # higher quality score and non-null earnings yield.
            mf_val = float(mf) if mf else 1.0
            strength = float(q or 0) * _m.log10(max(1.0, 10000.0 / mf_val))
            signals.append(
                Signal(
                    ticker=ticker,
                    signal_date=d.isoformat(),
                    direction=1,
                    label=f"Q={q}, MF={mf}",
                    strength=max(strength, 0.1),
                )
            )
    return signals


PRESETS = {
    "cluster_buys":  {"label": "Form 4 cluster buys (≥2 insiders, ≥$50k)", "fn": insider_cluster_buys},
    "csuite_buys":   {"label": "Form 4 C-suite buys (≥$100k)",             "fn": csuite_insider_buys},
    "form144_cluster_sells": {"label": "Form 144 cluster sells (≥2 sellers, ≥$100k)", "fn": form144_cluster_sells},
    "schedule_13d":  {"label": "Schedule 13D filings (activist 5%+ stakes)", "fn": schedule_13d_filings},
    "late_filings":  {"label": "NT 10-K / NT 10-Q late filings (short)",   "fn": late_filings_short},
    "proxy_contests":{"label": "Activist proxy contests (DFAN/DEFC/PRRN14A)", "fn": proxy_contests_long},
    "secondaries":   {"label": "S-3 / 424B secondary offerings (short)", "fn": secondaries_short},
    "tender_offers": {"label": "Tender-offer targets (SC TO-T / 14D9) (long)", "fn": tender_offers_long},
    "schedule_13g":  {"label": "Schedule 13G passive 5%+ stakes", "fn": schedule_13g_long},
    "form3":         {"label": "Form 3 new-insider onboarding", "fn": form3_long},
    "delistings":    {"label": "Form 15 / 25 delistings (short)", "fn": delistings_short},
    "auditor_changes": {"label": "8-K Item 4.01 auditor changes (short — red flag)", "fn": auditor_changes_short},
    "piotroski_long": {"label": "Piotroski top-decile quality + Magic Formula (quarterly rebalance, Note: uses current snapshot)", "fn": piotroski_long},
    # Stacked / combined signals — intersections of two sources
    "stacked_cluster_13d":    {"label": "STACK: cluster buys ∩ 13D (±10d)",    "fn": cluster_buys_plus_13d},
    "stacked_cluster_csuite": {"label": "STACK: cluster buys ∩ C-suite (±3d)", "fn": cluster_buys_plus_csuite},
    "stacked_csuite_13d":     {"label": "STACK: C-suite buys ∩ 13D (±10d)",    "fn": csuite_plus_13d},
}

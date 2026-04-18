"""Local DuckDB backing store for SEC DERA bulk data.

Ingest model: download one quarterly ZIP per dataset per quarter, load TSVs
into permanent DuckDB tables with typed columns. Queries go against local DB;
SEC is touched once per quarter.
"""
from __future__ import annotations

import datetime as dt
import zipfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}

ROOT = Path(__file__).parent
DB_PATH = ROOT / "seclab.duckdb"
CACHE_DIR = ROOT / ".cache" / "dera"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

INSIDER_URL = (
    "https://www.sec.gov/files/structureddata/data/"
    "insider-transactions-data-sets/{y}q{q}_form345.zip"
)
FSDS_URL = (
    "https://www.sec.gov/files/dera/data/"
    "financial-statement-data-sets/{y}q{q}.zip"
)
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS _ingested_quarters (
    dataset TEXT NOT NULL,       -- 'insider' or 'fsds'
    year    INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    ingested_at TIMESTAMP NOT NULL,
    PRIMARY KEY (dataset, year, quarter)
);

CREATE TABLE IF NOT EXISTS insider_submission (
    ACCESSION_NUMBER       TEXT PRIMARY KEY,
    FILING_DATE            DATE,
    PERIOD_OF_REPORT       DATE,
    DOCUMENT_TYPE          TEXT,
    ISSUERCIK              TEXT,
    ISSUERNAME             TEXT,
    ISSUERTRADINGSYMBOL    TEXT
);

CREATE TABLE IF NOT EXISTS insider_nonderiv_trans (
    ACCESSION_NUMBER       TEXT,
    NONDERIV_TRANS_SK      BIGINT,
    SECURITY_TITLE         TEXT,
    TRANS_DATE             DATE,
    TRANS_CODE             TEXT,
    TRANS_SHARES           DOUBLE,
    TRANS_PRICEPERSHARE    DOUBLE,
    TRANS_ACQUIRED_DISP_CD TEXT,
    SHRS_OWND_FOLWNG_TRANS DOUBLE,
    DIRECT_INDIRECT_OWNERSHIP TEXT
);

CREATE INDEX IF NOT EXISTS insider_nonderiv_trans_acc ON insider_nonderiv_trans(ACCESSION_NUMBER);
CREATE INDEX IF NOT EXISTS insider_nonderiv_trans_date ON insider_nonderiv_trans(TRANS_DATE);

CREATE TABLE IF NOT EXISTS insider_reporting_owner (
    ACCESSION_NUMBER        TEXT,
    RPTOWNERCIK             TEXT,
    RPTOWNERNAME            TEXT,
    RPTOWNER_RELATIONSHIP   TEXT,
    RPTOWNER_TITLE          TEXT
);

CREATE INDEX IF NOT EXISTS insider_reporting_owner_acc ON insider_reporting_owner(ACCESSION_NUMBER);

-- ─── XBRL Financial Statement Data Sets (10-K / 10-Q fundamentals) ──────────

CREATE TABLE IF NOT EXISTS fsds_sub (
    adsh        TEXT PRIMARY KEY,  -- accession number
    cik         INTEGER,
    name        TEXT,
    sic         INTEGER,
    form        TEXT,               -- 10-K, 10-Q, 10-K/A, etc.
    period      DATE,               -- period end date
    fy          INTEGER,
    fp          TEXT,               -- FY, Q1, Q2, Q3, Q4
    filed       DATE
);
CREATE INDEX IF NOT EXISTS fsds_sub_cik   ON fsds_sub(cik);
CREATE INDEX IF NOT EXISTS fsds_sub_form  ON fsds_sub(form);

CREATE TABLE IF NOT EXISTS fsds_num (
    adsh        TEXT,
    tag         TEXT,
    version     TEXT,
    ddate       DATE,
    qtrs        INTEGER,
    uom         TEXT,
    segments    TEXT,              -- NULL/'' = consolidated; non-empty = segment/geo/product breakdown
    coreg       TEXT,              -- co-registrant
    value       DOUBLE
);
CREATE INDEX IF NOT EXISTS fsds_num_adsh  ON fsds_num(adsh);
CREATE INDEX IF NOT EXISTS fsds_num_tag   ON fsds_num(tag);

CREATE TABLE IF NOT EXISTS fsds_tag (
    tag         TEXT,
    version     TEXT,
    custom      INTEGER,
    abstract    INTEGER,
    datatype    TEXT,
    iord        TEXT,
    crdr        TEXT,
    tlabel      TEXT,
    doc         TEXT,
    PRIMARY KEY (tag, version)
);

CREATE TABLE IF NOT EXISTS fsds_pre (
    adsh        TEXT,
    report      INTEGER,
    line        INTEGER,
    stmt        TEXT,               -- BS, IS, CF, EQ, CI, UN
    tag         TEXT,
    version     TEXT,
    plabel      TEXT
);
CREATE INDEX IF NOT EXISTS fsds_pre_adsh ON fsds_pre(adsh);

-- ─── Ticker ↔ CIK mapping (SEC company_tickers.json) ────────────────────────

CREATE TABLE IF NOT EXISTS ticker_map (
    ticker TEXT PRIMARY KEY,
    cik    INTEGER NOT NULL,
    name   TEXT
);
CREATE INDEX IF NOT EXISTS ticker_map_cik ON ticker_map(cik);

-- ─── Form 144 — planned insider sales (live-scraped) ────────────────────────

CREATE TABLE IF NOT EXISTS form144 (
    accession         TEXT PRIMARY KEY,
    filing_date       DATE,
    issuer_cik        INTEGER,
    issuer_name       TEXT,
    seller_name       TEXT,
    relationship      TEXT,
    class_title       TEXT,
    shares            DOUBLE,
    aggregate_value   DOUBLE,
    approx_sale_date  DATE,
    exchange          TEXT,
    broker_name       TEXT
);
CREATE INDEX IF NOT EXISTS form144_filing_date     ON form144(filing_date);
CREATE INDEX IF NOT EXISTS form144_issuer_cik      ON form144(issuer_cik);
CREATE INDEX IF NOT EXISTS form144_approx_sale_date ON form144(approx_sale_date);

CREATE TABLE IF NOT EXISTS _form144_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── Schedule 13D/A — activism tracker (live-scraped EFTS metadata) ─────────

CREATE TABLE IF NOT EXISTS thirteen_d (
    accession       TEXT PRIMARY KEY,
    form            TEXT,          -- 'SCHEDULE 13D' or 'SCHEDULE 13D/A'
    filing_date     DATE,
    issuer_cik      INTEGER,
    issuer_name     TEXT,
    issuer_ticker   TEXT,
    filer_cik       INTEGER,
    filer_name      TEXT,
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS thirteen_d_filing_date ON thirteen_d(filing_date);
CREATE INDEX IF NOT EXISTS thirteen_d_issuer_cik  ON thirteen_d(issuer_cik);
CREATE INDEX IF NOT EXISTS thirteen_d_filer_cik   ON thirteen_d(filer_cik);

CREATE TABLE IF NOT EXISTS _thirteen_d_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── Cross-sectional ratios snapshot for the screener ─────────────────────

CREATE TABLE IF NOT EXISTS ratios_snapshot (
    ticker                TEXT PRIMARY KEY,
    cik                   INTEGER,
    name                  TEXT,
    period                DATE,          -- fiscal period of latest 10-K
    price                 DOUBLE,
    market_cap            DOUBLE,
    enterprise_value      DOUBLE,
    pe_ratio              DOUBLE,
    pb_ratio              DOUBLE,
    ps_ratio              DOUBLE,
    ev_ebitda             DOUBLE,
    ev_revenue            DOUBLE,
    fcf_yield             DOUBLE,
    earnings_yield        DOUBLE,
    gross_margin          DOUBLE,
    operating_margin      DOUBLE,
    net_margin            DOUBLE,
    roe                   DOUBLE,
    roa                   DOUBLE,
    debt_to_equity        DOUBLE,
    debt_to_ebitda        DOUBLE,
    net_debt              DOUBLE,
    revenue_growth_yoy    DOUBLE,
    net_income_growth_yoy DOUBLE,
    revenue               DOUBLE,
    net_income            DOUBLE,
    computed_at           TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ratios_snapshot_market_cap ON ratios_snapshot(market_cap);
CREATE INDEX IF NOT EXISTS ratios_snapshot_pe         ON ratios_snapshot(pe_ratio);

-- ─── 8-K material events (live-scraped EFTS metadata + item codes) ──────────

CREATE TABLE IF NOT EXISTS form_8k (
    accession       TEXT PRIMARY KEY,
    filing_date     DATE,
    issuer_cik      INTEGER,
    issuer_name     TEXT,
    issuer_ticker   TEXT,
    items           TEXT,          -- comma-separated item codes for indexed search
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS form_8k_filing_date ON form_8k(filing_date);
CREATE INDEX IF NOT EXISTS form_8k_issuer_cik  ON form_8k(issuer_cik);
CREATE INDEX IF NOT EXISTS form_8k_items       ON form_8k(items);

CREATE TABLE IF NOT EXISTS _form_8k_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── NT 10-K / NT 10-Q late filings ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS late_filings (
    accession       TEXT PRIMARY KEY,
    form            TEXT,          -- 'NT 10-K', 'NT 10-Q', 'NT 10-K/A', 'NT 10-Q/A'
    filing_date     DATE,
    issuer_cik      INTEGER,
    issuer_name     TEXT,
    issuer_ticker   TEXT,
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS late_filings_filing_date ON late_filings(filing_date);
CREATE INDEX IF NOT EXISTS late_filings_issuer_cik  ON late_filings(issuer_cik);

CREATE TABLE IF NOT EXISTS _late_filings_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── 13F superinvestor holdings ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fund_meta (
    cik         INTEGER PRIMARY KEY,
    label       TEXT,           -- short name for UI ('Berkshire')
    long_name   TEXT,           -- full entity name
    added_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fund_holdings (
    filer_cik   INTEGER,
    period_end  DATE,
    accession   TEXT,
    cusip       TEXT,
    issuer_name TEXT,
    title_of_class TEXT,
    value_usd   DOUBLE,
    shares      DOUBLE,
    put_call    TEXT,
    PRIMARY KEY (filer_cik, period_end, cusip, put_call, title_of_class)
);
CREATE INDEX IF NOT EXISTS fund_holdings_filer  ON fund_holdings(filer_cik);
CREATE INDEX IF NOT EXISTS fund_holdings_period ON fund_holdings(period_end);
CREATE INDEX IF NOT EXISTS fund_holdings_cusip  ON fund_holdings(cusip);

CREATE TABLE IF NOT EXISTS _fund_ingested (
    filer_cik   INTEGER,
    period_end  DATE,
    ingested_at TIMESTAMP,
    PRIMARY KEY (filer_cik, period_end)
);

-- ─── Activist proxy contests (DFAN14A / DEFC14A / PRRN14A / DFRN14A) ───────

CREATE TABLE IF NOT EXISTS proxy_contests (
    accession       TEXT PRIMARY KEY,
    form            TEXT,
    filing_date     DATE,
    issuer_cik      INTEGER,
    issuer_name     TEXT,
    issuer_ticker   TEXT,
    filer_cik       INTEGER,
    filer_name      TEXT,
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS proxy_contests_filing_date ON proxy_contests(filing_date);
CREATE INDEX IF NOT EXISTS proxy_contests_issuer_cik  ON proxy_contests(issuer_cik);

CREATE TABLE IF NOT EXISTS _proxy_contests_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── Secondary offerings (S-3 / 424B) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS secondaries (
    accession       TEXT PRIMARY KEY,
    form            TEXT,
    filing_date     DATE,
    issuer_cik      INTEGER,
    issuer_name     TEXT,
    issuer_ticker   TEXT,
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS secondaries_filing_date ON secondaries(filing_date);
CREATE INDEX IF NOT EXISTS secondaries_issuer_cik  ON secondaries(issuer_cik);

CREATE TABLE IF NOT EXISTS _secondaries_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── Tender offers (SC TO-T / SC 14D9 / SC TO-I) ───────────────────────────

CREATE TABLE IF NOT EXISTS tender_offers (
    accession       TEXT PRIMARY KEY,
    form            TEXT,
    filing_date     DATE,
    subject_cik     INTEGER,
    subject_name    TEXT,
    subject_ticker  TEXT,
    filer_cik       INTEGER,
    filer_name      TEXT,
    primary_doc     TEXT
);
CREATE INDEX IF NOT EXISTS tender_offers_filing_date ON tender_offers(filing_date);
CREATE INDEX IF NOT EXISTS tender_offers_subject_cik ON tender_offers(subject_cik);

CREATE TABLE IF NOT EXISTS _tender_offers_ingested_days (
    day         DATE PRIMARY KEY,
    ingested_at TIMESTAMP NOT NULL
);

-- ─── Shared schema for three more filing types ────────────────────────────

CREATE TABLE IF NOT EXISTS schedule_13g (
    accession TEXT PRIMARY KEY, form TEXT, filing_date DATE,
    issuer_cik INTEGER, issuer_name TEXT, issuer_ticker TEXT,
    filer_cik INTEGER, filer_name TEXT, primary_doc TEXT
);
CREATE INDEX IF NOT EXISTS schedule_13g_filing_date ON schedule_13g(filing_date);
CREATE INDEX IF NOT EXISTS schedule_13g_issuer_cik  ON schedule_13g(issuer_cik);

CREATE TABLE IF NOT EXISTS form3_initials (
    accession TEXT PRIMARY KEY, form TEXT, filing_date DATE,
    issuer_cik INTEGER, issuer_name TEXT, issuer_ticker TEXT,
    filer_cik INTEGER, filer_name TEXT, primary_doc TEXT
);
CREATE INDEX IF NOT EXISTS form3_filing_date ON form3_initials(filing_date);
CREATE INDEX IF NOT EXISTS form3_issuer_cik  ON form3_initials(issuer_cik);

CREATE TABLE IF NOT EXISTS form_d (
    accession TEXT PRIMARY KEY, form TEXT, filing_date DATE,
    issuer_cik INTEGER, issuer_name TEXT, issuer_ticker TEXT,
    filer_cik INTEGER, filer_name TEXT, primary_doc TEXT
);
CREATE INDEX IF NOT EXISTS form_d_filing_date ON form_d(filing_date);
CREATE INDEX IF NOT EXISTS form_d_issuer_cik  ON form_d(issuer_cik);

CREATE TABLE IF NOT EXISTS _schedule_13g_ingested_days (day DATE PRIMARY KEY, ingested_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS _form3_ingested_days (day DATE PRIMARY KEY, ingested_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS _form_d_ingested_days (day DATE PRIMARY KEY, ingested_at TIMESTAMP);

-- ─── Delistings (Form 15 + Form 25) and foreign-issuer filings (20-F / 6-K) ─

CREATE TABLE IF NOT EXISTS delistings (
    accession TEXT PRIMARY KEY, form TEXT, filing_date DATE,
    issuer_cik INTEGER, issuer_name TEXT, issuer_ticker TEXT,
    filer_cik INTEGER, filer_name TEXT, primary_doc TEXT
);
CREATE INDEX IF NOT EXISTS delistings_filing_date ON delistings(filing_date);
CREATE INDEX IF NOT EXISTS delistings_issuer_cik  ON delistings(issuer_cik);

CREATE TABLE IF NOT EXISTS foreign_filings (
    accession TEXT PRIMARY KEY, form TEXT, filing_date DATE,
    issuer_cik INTEGER, issuer_name TEXT, issuer_ticker TEXT,
    filer_cik INTEGER, filer_name TEXT, primary_doc TEXT
);
CREATE INDEX IF NOT EXISTS foreign_filings_filing_date ON foreign_filings(filing_date);
CREATE INDEX IF NOT EXISTS foreign_filings_issuer_cik  ON foreign_filings(issuer_cik);

CREATE TABLE IF NOT EXISTS _delistings_ingested_days (day DATE PRIMARY KEY, ingested_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS _foreign_filings_ingested_days (day DATE PRIMARY KEY, ingested_at TIMESTAMP);

-- ─── Paper / forward-test portfolios ─────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS paper_portfolio_id_seq;
CREATE TABLE IF NOT EXISTS paper_portfolio (
    id             INTEGER PRIMARY KEY DEFAULT nextval('paper_portfolio_id_seq'),
    name           TEXT UNIQUE NOT NULL,
    preset         TEXT NOT NULL,
    config_json    TEXT NOT NULL,           -- full backtest params
    started_at     DATE NOT NULL,
    starting_cash  DOUBLE NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    last_updated   DATE,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_trade (
    portfolio_id    INTEGER NOT NULL,
    trade_id        INTEGER NOT NULL,       -- dense per-portfolio index
    ticker          TEXT NOT NULL,
    signal_date     DATE NOT NULL,
    entry_date      DATE NOT NULL,
    entry_price     DOUBLE NOT NULL,
    shares          DOUBLE NOT NULL,
    position_value  DOUBLE NOT NULL,
    exit_date       DATE,                   -- NULL while open
    exit_price      DOUBLE,
    exit_reason     TEXT,
    pnl             DOUBLE,
    return_pct      DOUBLE,
    label           TEXT,
    strength        DOUBLE DEFAULT 1.0,
    PRIMARY KEY (portfolio_id, trade_id)
);
CREATE INDEX IF NOT EXISTS paper_trade_portfolio ON paper_trade(portfolio_id);
CREATE INDEX IF NOT EXISTS paper_trade_open ON paper_trade(portfolio_id, exit_date);

CREATE TABLE IF NOT EXISTS paper_equity (
    portfolio_id  INTEGER NOT NULL,
    date          DATE NOT NULL,
    equity        DOUBLE NOT NULL,
    cash          DOUBLE NOT NULL,
    invested      DOUBLE NOT NULL,
    PRIMARY KEY (portfolio_id, date)
);
"""


@dataclass
class Quarter:
    year: int
    quarter: int

    def __str__(self) -> str:
        return f"{self.year}Q{self.quarter}"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH))
    con.execute(SCHEMA_SQL)
    return con


# ─── Download + extract ─────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    with httpx.Client(headers=HEADERS, timeout=300, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def _extract_dir(zip_path: Path) -> Path:
    out = zip_path.with_suffix("")
    out.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            target = out / name
            if not target.exists():
                target.write_bytes(zf.read(name))
    return out


# ─── Insider (Form 3/4/5) ingest ────────────────────────────────────────────

def _quarter_already_ingested(con: duckdb.DuckDBPyConnection, dataset: str, q: Quarter) -> bool:
    row = con.execute(
        "SELECT 1 FROM _ingested_quarters WHERE dataset=? AND year=? AND quarter=?",
        [dataset, q.year, q.quarter],
    ).fetchone()
    return row is not None


def _mark_ingested(con: duckdb.DuckDBPyConnection, dataset: str, q: Quarter) -> None:
    con.execute(
        "INSERT OR REPLACE INTO _ingested_quarters VALUES (?, ?, ?, ?)",
        [dataset, q.year, q.quarter, dt.datetime.now()],
    )


def ingest_insider_quarter(q: Quarter, *, force: bool = False) -> bool:
    """Pull one quarter of Insider Transactions Data Set into DuckDB.

    Returns True if newly ingested, False if already present.
    """
    con = connect()
    if not force and _quarter_already_ingested(con, "insider", q):
        return False

    url = INSIDER_URL.format(y=q.year, q=q.quarter)
    zip_path = CACHE_DIR / f"insider_{q.year}q{q.quarter}.zip"
    _download(url, zip_path)
    src = _extract_dir(zip_path)

    # DuckDB reads the TSVs directly via read_csv. DERA dates are 'DD-MON-YYYY'.
    # delete any existing rows for this quarter first, then insert.
    con.execute(
        """
        DELETE FROM insider_submission
         WHERE ACCESSION_NUMBER IN (
            SELECT ACCESSION_NUMBER FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
         );
        """,
        [str(src / "SUBMISSION.tsv")],
    )
    con.execute(
        """
        INSERT INTO insider_submission
        SELECT
            ACCESSION_NUMBER,
            TRY_STRPTIME(FILING_DATE, '%d-%b-%Y')::DATE AS FILING_DATE,
            TRY_STRPTIME(PERIOD_OF_REPORT, '%d-%b-%Y')::DATE AS PERIOD_OF_REPORT,
            DOCUMENT_TYPE,
            ISSUERCIK,
            ISSUERNAME,
            ISSUERTRADINGSYMBOL
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true);
        """,
        [str(src / "SUBMISSION.tsv")],
    )

    con.execute(
        """
        DELETE FROM insider_nonderiv_trans
         WHERE ACCESSION_NUMBER IN (
            SELECT ACCESSION_NUMBER FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
         );
        """,
        [str(src / "NONDERIV_TRANS.tsv")],
    )
    con.execute(
        """
        INSERT INTO insider_nonderiv_trans
        SELECT
            ACCESSION_NUMBER,
            TRY_CAST(NONDERIV_TRANS_SK AS BIGINT),
            SECURITY_TITLE,
            TRY_STRPTIME(TRANS_DATE, '%d-%b-%Y')::DATE,
            TRANS_CODE,
            TRY_CAST(TRANS_SHARES AS DOUBLE),
            TRY_CAST(TRANS_PRICEPERSHARE AS DOUBLE),
            TRANS_ACQUIRED_DISP_CD,
            TRY_CAST(SHRS_OWND_FOLWNG_TRANS AS DOUBLE),
            DIRECT_INDIRECT_OWNERSHIP
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true);
        """,
        [str(src / "NONDERIV_TRANS.tsv")],
    )

    con.execute(
        """
        DELETE FROM insider_reporting_owner
         WHERE ACCESSION_NUMBER IN (
            SELECT ACCESSION_NUMBER FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
         );
        """,
        [str(src / "REPORTINGOWNER.tsv")],
    )
    con.execute(
        """
        INSERT INTO insider_reporting_owner
        SELECT
            ACCESSION_NUMBER,
            RPTOWNERCIK,
            RPTOWNERNAME,
            RPTOWNER_RELATIONSHIP,
            RPTOWNER_TITLE
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true);
        """,
        [str(src / "REPORTINGOWNER.tsv")],
    )

    _mark_ingested(con, "insider", q)
    con.close()
    return True


# ─── Quarter coverage ───────────────────────────────────────────────────────

def _quarters_in_range(start: dt.date, end: dt.date) -> list[Quarter]:
    y, q = start.year, (start.month - 1) // 3 + 1
    end_y, end_q = end.year, (end.month - 1) // 3 + 1
    out: list[Quarter] = []
    while (y, q) <= (end_y, end_q):
        out.append(Quarter(y, q))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def _current_quarter() -> Quarter:
    t = dt.date.today()
    return Quarter(t.year, (t.month - 1) // 3 + 1)


def ensure_insider_quarters(start: str, end: str) -> dict:
    """Make sure every quarter the window touches is in the DB.

    Skips the currently-in-progress quarter — DERA hasn't released it yet.
    Returns {covered: [Q], missing_current: Q or None, newly_ingested: [Q]}.
    """
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    current = _current_quarter()
    quarters = _quarters_in_range(start_d, end_d)

    covered: list[Quarter] = []
    newly: list[Quarter] = []
    missing_current: Quarter | None = None

    for q in quarters:
        if (q.year, q.quarter) >= (current.year, current.quarter):
            missing_current = q
            continue
        did_ingest = ingest_insider_quarter(q)
        covered.append(q)
        if did_ingest:
            newly.append(q)

    return {
        "covered": [str(q) for q in covered],
        "missing_current": str(missing_current) if missing_current else None,
        "newly_ingested": [str(q) for q in newly],
    }


# ─── Query: insider purchases (code 'P') ────────────────────────────────────

_C_SUITE_TITLES = ("CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL", "PRESIDENT")


def get_insider_purchases(start: str, end: str, top: int = 25, min_value: float = 0) -> dict:
    """Return the aggregated leaderboard + raw transactions for a window.

    Shape matches the API layer expectations: summary, leaderboard, transactions.
    """
    con = connect()
    # Sanity filters strip BDCs / private funds (no ticker) and data-entry
    # errors where filers put a total value in the per-share field.
    raw_rows = con.execute(
        """
        SELECT
            tx.TRANS_DATE             AS date,
            sub.ISSUERTRADINGSYMBOL   AS ticker,
            sub.ISSUERNAME            AS issuer,
            sub.ISSUERCIK             AS cik,
            tx.TRANS_SHARES           AS shares,
            tx.TRANS_PRICEPERSHARE    AS price,
            tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE AS value,
            tx.ACCESSION_NUMBER       AS accession
        FROM insider_nonderiv_trans tx
        JOIN insider_submission sub USING (ACCESSION_NUMBER)
        WHERE tx.TRANS_CODE = 'P'
          AND tx.TRANS_DATE BETWEEN ? AND ?
          AND tx.TRANS_SHARES IS NOT NULL AND tx.TRANS_SHARES > 0
          AND tx.TRANS_PRICEPERSHARE IS NOT NULL
          AND tx.TRANS_PRICEPERSHARE BETWEEN 0.01 AND 10000
          AND sub.ISSUERTRADINGSYMBOL IS NOT NULL
          AND UPPER(sub.ISSUERTRADINGSYMBOL) NOT IN ('NONE', 'N/A', '')
        ORDER BY value DESC
        """,
        [start, end],
    ).fetchall()
    cols = ["date", "ticker", "issuer", "cik", "shares", "price", "value", "accession"]
    transactions = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in raw_rows
    ]

    if not transactions:
        con.close()
        return {
            "summary": {
                "purchase_transactions": 0,
                "total_value": 0.0,
                "cluster_tickers": 0,
                "csuite_tickers": 0,
            },
            "leaderboard": [],
            "transactions": [],
        }

    owners = con.execute(
        """
        SELECT ACCESSION_NUMBER, RPTOWNERNAME, RPTOWNER_RELATIONSHIP, RPTOWNER_TITLE
        FROM insider_reporting_owner
        WHERE ACCESSION_NUMBER IN (SELECT DISTINCT ACCESSION_NUMBER
                                    FROM insider_nonderiv_trans
                                    WHERE TRANS_CODE='P' AND TRANS_DATE BETWEEN ? AND ?)
        """,
        [start, end],
    ).fetchall()
    owner_by_acc: dict[str, list[dict]] = {}
    for acc, name, rel, title in owners:
        position = title if title else (rel or "")
        owner_by_acc.setdefault(acc, []).append({"name": name or "", "position": position})
    con.close()

    # Aggregate by ticker
    per_ticker: dict[str, dict] = {}
    for tx in transactions:
        t = tx["ticker"] or "(none)"
        e = per_ticker.setdefault(
            t,
            {
                "ticker": t,
                "issuer": (tx["issuer"] or "")[:40],
                "total_value": 0.0,
                "shares": 0.0,
                "insiders": set(),
                "positions": set(),
            },
        )
        e["total_value"] += tx["value"] or 0.0
        e["shares"] += tx["shares"] or 0.0
        for o in owner_by_acc.get(tx["accession"], []):
            if o["position"]:
                e["positions"].add(o["position"])
            e["insiders"].add(o["name"] or tx["accession"])

    leaderboard = []
    for t, e in per_ticker.items():
        if e["total_value"] < min_value:
            continue
        positions_joined = ", ".join(sorted(e["positions"]))[:80]
        csuite = "✓" if any(k in positions_joined.upper() for k in _C_SUITE_TITLES) else ""
        leaderboard.append(
            {
                "ticker": t,
                "issuer": e["issuer"],
                "total_$": f"${e['total_value']:,.0f}",
                "shares": f"{e['shares']:,.0f}",
                "# insiders": len(e["insiders"]),
                "C-suite": csuite,
                "positions": positions_joined,
                "value_num": e["total_value"],
            }
        )
    leaderboard.sort(key=lambda r: (-r["# insiders"], -r["value_num"]))
    leaderboard = leaderboard[:top]

    total_value = sum(t["value"] or 0.0 for t in transactions)
    return {
        "summary": {
            "purchase_transactions": len(transactions),
            "total_value": total_value,
            "cluster_tickers": sum(1 for r in leaderboard if r["# insiders"] >= 2),
            "csuite_tickers": sum(1 for r in leaderboard if r["C-suite"] == "✓"),
        },
        "leaderboard": leaderboard,
        "transactions": transactions,
    }


# ─── XBRL fundamentals ingest ───────────────────────────────────────────────

def ingest_fundamentals_quarter(q: Quarter, *, force: bool = False) -> bool:
    """Pull one quarter of XBRL Financial Statement Data Sets into DuckDB."""
    con = connect()
    if not force and _quarter_already_ingested(con, "fsds", q):
        con.close()
        return False

    url = FSDS_URL.format(y=q.year, q=q.quarter)
    zip_path = CACHE_DIR / f"fsds_{q.year}q{q.quarter}.zip"
    _download(url, zip_path)
    src = _extract_dir(zip_path)

    # sub.txt — one row per filing submission
    con.execute(
        """
        DELETE FROM fsds_sub WHERE adsh IN (
            SELECT adsh FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
        );
        """,
        [str(src / "sub.txt")],
    )
    con.execute(
        """
        INSERT INTO fsds_sub
        SELECT
            adsh,
            TRY_CAST(cik AS INTEGER),
            name,
            TRY_CAST(sic AS INTEGER),
            form,
            TRY_STRPTIME(period, '%Y%m%d')::DATE,
            TRY_CAST(fy AS INTEGER),
            fp,
            TRY_STRPTIME(filed, '%Y%m%d')::DATE
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true);
        """,
        [str(src / "sub.txt")],
    )

    # num.txt — the numbers (biggest table)
    con.execute(
        """
        DELETE FROM fsds_num WHERE adsh IN (
            SELECT adsh FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
        );
        """,
        [str(src / "sub.txt")],
    )
    con.execute(
        """
        INSERT INTO fsds_num
        SELECT
            adsh, tag, version,
            TRY_STRPTIME(ddate, '%Y%m%d')::DATE,
            TRY_CAST(qtrs AS INTEGER),
            uom,
            segments,
            coreg,
            TRY_CAST(value AS DOUBLE)
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true, ignore_errors=true);
        """,
        [str(src / "num.txt")],
    )

    # tag.txt — tag dictionary (update only, duplicates fine)
    con.execute(
        """
        INSERT OR REPLACE INTO fsds_tag
        SELECT
            tag, version,
            TRY_CAST(custom AS INTEGER),
            TRY_CAST(abstract AS INTEGER),
            datatype, iord, crdr, tlabel, doc
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true, ignore_errors=true);
        """,
        [str(src / "tag.txt")],
    )

    # pre.txt — presentation (which statement each tag belongs to)
    con.execute(
        """
        DELETE FROM fsds_pre WHERE adsh IN (
            SELECT adsh FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true)
        );
        """,
        [str(src / "sub.txt")],
    )
    con.execute(
        """
        INSERT INTO fsds_pre
        SELECT
            adsh,
            TRY_CAST(report AS INTEGER),
            TRY_CAST(line AS INTEGER),
            stmt, tag, version, plabel
        FROM read_csv_auto(?, delim='\t', header=true, all_varchar=true, ignore_errors=true);
        """,
        [str(src / "pre.txt")],
    )

    _mark_ingested(con, "fsds", q)
    con.close()
    return True


def ensure_fundamentals_quarters(start: str, end: str) -> dict:
    """Ingest every FSDS quarter the window touches that isn't current."""
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    current = _current_quarter()
    quarters = _quarters_in_range(start_d, end_d)
    covered: list[Quarter] = []
    newly: list[Quarter] = []
    missing_current: Quarter | None = None
    for q in quarters:
        if (q.year, q.quarter) >= (current.year, current.quarter):
            missing_current = q
            continue
        did_ingest = ingest_fundamentals_quarter(q)
        covered.append(q)
        if did_ingest:
            newly.append(q)
    return {
        "covered": [str(q) for q in covered],
        "missing_current": str(missing_current) if missing_current else None,
        "newly_ingested": [str(q) for q in newly],
    }


# ─── Ticker ↔ CIK mapping ───────────────────────────────────────────────────

def refresh_ticker_map() -> int:
    """Download SEC company_tickers.json and replace ticker_map table."""
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        r = client.get(TICKER_MAP_URL)
        r.raise_for_status()
        data = r.json()
    # data is {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC"}, ...}
    rows = [
        (str(row["ticker"]).upper(), int(row["cik_str"]), row.get("title", ""))
        for row in data.values()
    ]
    con = connect()
    con.execute("DELETE FROM ticker_map;")
    con.executemany("INSERT INTO ticker_map VALUES (?, ?, ?);", rows)
    con.close()
    return len(rows)


def ticker_to_cik(ticker: str) -> int | None:
    con = connect()
    row = con.execute("SELECT cik FROM ticker_map WHERE ticker = ?", [ticker.upper()]).fetchone()
    con.close()
    return int(row[0]) if row else None


def search_tickers(q: str, limit: int = 20) -> list[dict]:
    """Search by ticker prefix or name substring. Ticker matches rank above name matches."""
    q = (q or "").strip()
    if not q:
        return []
    con = connect()
    qu = q.upper()
    # Prefix match on ticker, case-insensitive substring on name
    rows = con.execute(
        """
        SELECT ticker, cik, name,
               CASE
                 WHEN ticker = ? THEN 0
                 WHEN ticker LIKE ? THEN 1
                 WHEN UPPER(name) LIKE ? THEN 2
                 WHEN UPPER(name) LIKE ? THEN 3
                 ELSE 4
               END AS rank
        FROM ticker_map
        WHERE ticker LIKE ? OR UPPER(name) LIKE ?
        ORDER BY rank, LENGTH(ticker), ticker
        LIMIT ?
        """,
        [qu, f"{qu}%", f"{qu}%", f"%{qu}%", f"{qu}%", f"%{qu}%", limit],
    ).fetchall()
    con.close()
    return [{"ticker": t, "cik": c, "name": n} for t, c, n, _ in rows]


# ─── Fundamentals query ─────────────────────────────────────────────────────

def get_fundamentals(ticker: str, years: int = 5) -> dict:
    """Return annual + most-recent quarterly fundamentals for a ticker.

    Groups by (statement, tag) so the caller can render IS/BS/CF tables.
    """
    cik = ticker_to_cik(ticker)
    if cik is None:
        return {"error": f"unknown ticker {ticker}", "ticker": ticker.upper()}

    con = connect()
    company = con.execute(
        "SELECT name FROM fsds_sub WHERE cik = ? ORDER BY filed DESC LIMIT 1", [cik]
    ).fetchone()
    company_name = company[0] if company else ""

    # Annual (10-K) filings — one row per fiscal-year, one value per tag per year.
    # Prefer unit = 'USD' or 'shares' for display; skip segments/coreg (consolidated).
    since = dt.date.today().replace(year=dt.date.today().year - years).isoformat()
    # qtrs: 0=instant (BS), 4=annual (IS/CF for 10-K)
    # segments IS NULL → consolidated total (drops per-segment/geo/product breakdowns)
    # coreg IS NULL → primary registrant (drops co-registered subsidiaries)
    # ddate >= since → grabs the 2-year comparatives every 10-K carries, not just current fy
    annual = con.execute(
        """
        SELECT n.ddate, s.period, s.adsh, p.stmt, p.plabel, n.tag, n.uom, n.value
        FROM fsds_sub s
        JOIN fsds_num n USING (adsh)
        JOIN fsds_pre p USING (adsh, tag, version)
        WHERE s.cik = ?
          AND s.form = '10-K'
          AND n.ddate >= ?
          AND (n.qtrs IS NULL OR n.qtrs IN (0, 4))
          AND n.uom IN ('USD', 'shares', 'USD/shares')
          AND p.stmt IN ('IS', 'BS', 'CF')
          AND (n.segments IS NULL OR n.segments = '')
          AND (n.coreg IS NULL OR n.coreg = '')
        ORDER BY n.ddate DESC, p.stmt, p.line
        """,
        [cik, since],
    ).fetchall()

    # Shape: {stmt: {tag: {label, rows: [{fy, period, value, uom}]}}}
    # Group by fiscal-year ENDING in that calendar year (from ddate), so comparatives
    # from older 10-Ks match current-year comparatives on the same ticker.
    statements: dict[str, dict[str, dict]] = {"IS": {}, "BS": {}, "CF": {}}
    seen: dict[tuple[str, str, int], bool] = {}  # (stmt, tag, year) → dedupe
    for ddate, period, adsh, stmt, plabel, tag, uom, value in annual:
        if stmt not in statements or ddate is None:
            continue
        key = (stmt, tag, ddate.year)
        if key in seen:
            continue
        seen[key] = True
        item = statements[stmt].setdefault(
            tag, {"label": plabel or tag, "uom": uom, "rows": []}
        )
        item["rows"].append(
            {"fy": ddate.year, "period": ddate.isoformat(), "value": value}
        )

    # Latest quarterly filing for a "most recent quarter" snapshot
    latest_q = con.execute(
        """
        SELECT adsh, period, fp, filed FROM fsds_sub
        WHERE cik = ? AND form IN ('10-Q','10-Q/A')
        ORDER BY period DESC, filed DESC LIMIT 1
        """,
        [cik],
    ).fetchone()

    latest_q_rows: list[dict] = []
    if latest_q:
        adsh, period, fp, filed = latest_q
        latest_q_rows = [
            {
                "tag": t,
                "label": pl or t,
                "stmt": stmt,
                "value": v,
                "uom": u,
                "period": period.isoformat() if period else None,
                "fp": fp,
            }
            for t, stmt, pl, v, u in con.execute(
                """
                SELECT p.tag, p.stmt, p.plabel, n.value, n.uom
                FROM fsds_num n
                JOIN fsds_pre p USING (adsh, tag, version)
                WHERE n.adsh = ?
                  AND n.uom IN ('USD','shares','USD/shares')
                  AND p.stmt IN ('IS','BS','CF')
                  AND (n.segments IS NULL OR n.segments = '')
                  AND (n.coreg IS NULL OR n.coreg = '')
                ORDER BY p.stmt, p.line
                """,
                [adsh],
            ).fetchall()
        ]

    con.close()
    return {
        "ticker": ticker.upper(),
        "cik": cik,
        "name": company_name,
        "statements": statements,
        "latest_quarter": {
            "period": latest_q[1].isoformat() if latest_q and latest_q[1] else None,
            "fp": latest_q[2] if latest_q else None,
            "filed": latest_q[3].isoformat() if latest_q and latest_q[3] else None,
            "rows": latest_q_rows,
        },
    }


# ─── Per-ticker dossier — every signal for one ticker ─────────────────────

def get_ticker_dossier(
    ticker: str,
    start: str,
    end: str,
    top_per_type: int = 30,
) -> dict:
    """Aggregate every SEC signal we have about a single ticker into one payload.

    Used by the /fundamentals/:ticker dossier tab. One DuckDB connection, N
    small queries. Most are ticker-filtered; insider/Form 144 go via CIK.
    """
    t = (ticker or "").upper()
    cik = ticker_to_cik(t)
    con = connect()

    def _rows(sql: str, params: list) -> list[dict]:
        cursor = con.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [
            {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
            for r in cursor.fetchall()
        ]

    insider_buys: list[dict] = []
    planned_sells: list[dict] = []
    if cik is not None:
        insider_buys = _rows(
            """
            SELECT tx.TRANS_DATE AS date, tx.TRANS_SHARES AS shares,
                   tx.TRANS_PRICEPERSHARE AS price,
                   tx.TRANS_SHARES * tx.TRANS_PRICEPERSHARE AS value,
                   own.RPTOWNERNAME AS insider_name,
                   COALESCE(own.RPTOWNER_TITLE, own.RPTOWNER_RELATIONSHIP, '') AS position
            FROM insider_nonderiv_trans tx
            JOIN insider_submission sub USING (ACCESSION_NUMBER)
            LEFT JOIN insider_reporting_owner own USING (ACCESSION_NUMBER)
            WHERE tx.TRANS_CODE = 'P'
              AND tx.TRANS_DATE BETWEEN ? AND ?
              AND CAST(sub.ISSUERCIK AS INTEGER) = ?
              AND tx.TRANS_SHARES > 0
              AND tx.TRANS_PRICEPERSHARE BETWEEN 0.01 AND 10000
            ORDER BY tx.TRANS_DATE DESC, value DESC
            LIMIT ?
            """,
            [start, end, cik, top_per_type * 2],
        )
        planned_sells = _rows(
            """
            SELECT filing_date, approx_sale_date, seller_name, relationship,
                   shares, aggregate_value, broker_name
            FROM form144
            WHERE issuer_cik = ?
              AND filing_date BETWEEN ? AND ?
              AND shares > 0 AND aggregate_value > 0
            ORDER BY filing_date DESC
            LIMIT ?
            """,
            [cik, start, end, top_per_type],
        )

    thirteen_d = _rows(
        """
        SELECT filing_date, form, filer_name, accession
        FROM thirteen_d
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    thirteen_g = _rows(
        """
        SELECT filing_date, form, filer_name, accession
        FROM schedule_13g
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    eight_k = _rows(
        """
        SELECT filing_date, items, accession
        FROM form_8k
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type * 2],
    )
    # items is comma-separated; split into list for the UI
    for row in eight_k:
        items_str = row.get("items") or ""
        row["items"] = [i for i in items_str.split(",") if i] if items_str else []
    late = _rows(
        """
        SELECT filing_date, form, accession
        FROM late_filings
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    proxy = _rows(
        """
        SELECT filing_date, form, filer_name, accession
        FROM proxy_contests
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    secondaries_rows = _rows(
        """
        SELECT filing_date, form, accession
        FROM secondaries
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    tender_rows = _rows(
        """
        SELECT filing_date, form, filer_name AS acquirer, accession
        FROM tender_offers
        WHERE UPPER(subject_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )
    form3 = _rows(
        """
        SELECT filing_date, filer_name, accession
        FROM form3_initials
        WHERE UPPER(issuer_ticker) = ? AND filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC LIMIT ?
        """,
        [t, start, end, top_per_type],
    )

    # Superinvestor funds holding this ticker at latest reported period.
    # We don't have CUSIP→ticker mapping, so fall back to name-based normalize match
    # (same approach as the superinvestors page).
    superinvestor_holders: list[dict] = []
    if cik is not None:
        # Look up normalized issuer name matches
        name_rows = con.execute(
            "SELECT DISTINCT issuer_name FROM fund_holdings"
        ).fetchall()
        target_key = None
        tm_row = con.execute(
            "SELECT name FROM ticker_map WHERE ticker = ?", [t]
        ).fetchone()
        if tm_row:
            target_key = _normalize_issuer_name(tm_row[0])
        matching_names = set()
        if target_key:
            for (iname,) in name_rows:
                if _normalize_issuer_name(iname) == target_key:
                    matching_names.add(iname)
        if matching_names:
            placeholders = ",".join("?" * len(matching_names))
            superinvestor_holders = _rows(
                f"""
                SELECT fm.label AS fund_label, fm.long_name AS fund_name,
                       h.period_end, h.value_usd, h.shares, h.put_call, h.issuer_name
                FROM fund_holdings h
                JOIN fund_meta fm ON fm.cik = h.filer_cik
                WHERE h.issuer_name IN ({placeholders})
                  AND h.period_end = (
                    SELECT MAX(period_end) FROM fund_holdings
                    WHERE filer_cik = h.filer_cik
                  )
                ORDER BY h.value_usd DESC
                LIMIT ?
                """,
                list(matching_names) + [top_per_type],
            )
    con.close()

    # Totals/summary
    total_bought = sum(r.get("value") or 0 for r in insider_buys)
    total_planned_sell = sum(r.get("aggregate_value") or 0 for r in planned_sells)
    return {
        "ticker": t,
        "cik": cik,
        "range": {"start": start, "end": end},
        "summary": {
            "insider_buys": len(insider_buys),
            "insider_buys_total": total_bought,
            "planned_sells": len(planned_sells),
            "planned_sells_total": total_planned_sell,
            "filings_13d": len(thirteen_d),
            "filings_13g": len(thirteen_g),
            "eight_k": len(eight_k),
            "late_filings": len(late),
            "proxy_contests": len(proxy),
            "secondaries": len(secondaries_rows),
            "tender_offers": len(tender_rows),
            "form3": len(form3),
            "superinvestor_holders": len(superinvestor_holders),
        },
        "insider_buys": insider_buys,
        "planned_sells": planned_sells,
        "thirteen_d": thirteen_d,
        "thirteen_g": thirteen_g,
        "eight_k": eight_k,
        "late_filings": late,
        "proxy_contests": proxy,
        "secondaries": secondaries_rows,
        "tender_offers": tender_rows,
        "form3": form3,
        "superinvestor_holders": superinvestor_holders,
    }


# ─── Key ratios from XBRL fundamentals + latest price ──────────────────────

# XBRL tag aliases — XBRL has many names for the same concept. Try in order.
_TAG_ALIASES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"],
    "eps_basic": ["EarningsPerShareBasic"],
    "shares_out": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "Cash",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "short_term_debt": [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "interest_expense": ["InterestExpense"],
}


def _pick(tag_values: dict[str, float | None], aliases: list[str]) -> float | None:
    for t in aliases:
        v = tag_values.get(t)
        if v is not None:
            return v
    return None


def get_key_ratios(ticker: str) -> dict:
    """Compute valuation, profitability, health ratios for a ticker.

    Uses the latest available 10-K in DuckDB + current price from yfinance.
    """
    cik = ticker_to_cik(ticker)
    if cik is None:
        return {"ticker": ticker.upper(), "error": "unknown ticker"}

    con = connect()
    latest = con.execute(
        """
        SELECT adsh, period, fy, name
        FROM fsds_sub
        WHERE cik = ? AND form = '10-K'
        ORDER BY period DESC, filed DESC
        LIMIT 1
        """,
        [cik],
    ).fetchone()
    if not latest:
        con.close()
        return {"ticker": ticker.upper(), "error": "no 10-K in DuckDB"}
    adsh, period, fy, name = latest

    # Prior year's 10-K (for YoY growth)
    prior = con.execute(
        """
        SELECT adsh, period, fy
        FROM fsds_sub
        WHERE cik = ? AND form = '10-K' AND period < ?
        ORDER BY period DESC LIMIT 1
        """,
        [cik, period],
    ).fetchone()

    # Fetch all tag values for both filings in one query
    adshes = [adsh] + ([prior[0]] if prior else [])
    rows = con.execute(
        """
        SELECT n.adsh, n.tag, n.uom, n.value, n.qtrs, n.ddate
        FROM fsds_num n
        WHERE n.adsh IN ({})
          AND (n.segments IS NULL OR n.segments = '')
          AND (n.coreg IS NULL OR n.coreg = '')
          AND n.uom IN ('USD', 'shares', 'USD/shares')
        """.format(",".join("?" * len(adshes))),
        adshes,
    ).fetchall()
    con.close()

    latest_vals: dict[str, float] = {}
    prior_vals: dict[str, float] = {}
    # Pick qtrs=4 for IS/CF (annual) or qtrs=0 for BS (instant), ddate closest to period
    for a, tag, uom, value, qtrs, ddate in rows:
        target = latest_vals if a == adsh else prior_vals
        target_period = period if a == adsh else (prior[1] if prior else None)
        # Only take tags whose ddate matches their filing's period
        if ddate != target_period:
            continue
        if qtrs not in (0, 4, None):
            continue
        # Keep most meaningful value per tag — prefer largest absolute (consolidated)
        existing = target.get(tag)
        if existing is None or abs(value or 0) > abs(existing):
            target[tag] = value

    latest_price = _latest_price(ticker)

    revenue = _pick(latest_vals, _TAG_ALIASES["revenue"])
    prior_revenue = _pick(prior_vals, _TAG_ALIASES["revenue"])
    net_income = _pick(latest_vals, _TAG_ALIASES["net_income"])
    prior_net_income = _pick(prior_vals, _TAG_ALIASES["net_income"])
    eps_d = _pick(latest_vals, _TAG_ALIASES["eps_diluted"]) or _pick(latest_vals, _TAG_ALIASES["eps_basic"])
    shares = _pick(latest_vals, _TAG_ALIASES["shares_out"])
    assets = _pick(latest_vals, _TAG_ALIASES["assets"])
    liabilities = _pick(latest_vals, _TAG_ALIASES["liabilities"])
    equity = _pick(latest_vals, _TAG_ALIASES["equity"])
    cash = _pick(latest_vals, _TAG_ALIASES["cash"])
    lt_debt = _pick(latest_vals, _TAG_ALIASES["long_term_debt"]) or 0.0
    st_debt = _pick(latest_vals, _TAG_ALIASES["short_term_debt"]) or 0.0
    total_debt = (lt_debt or 0) + (st_debt or 0)
    gross_profit = _pick(latest_vals, _TAG_ALIASES["gross_profit"])
    op_income = _pick(latest_vals, _TAG_ALIASES["operating_income"])
    cogs = _pick(latest_vals, _TAG_ALIASES["cogs"])
    cfo = _pick(latest_vals, _TAG_ALIASES["cfo"])
    capex = _pick(latest_vals, _TAG_ALIASES["capex"])
    d_and_a = _pick(latest_vals, _TAG_ALIASES["depreciation"])

    # Derived
    fcf = (cfo or 0) - abs(capex or 0) if cfo is not None else None
    ebitda = (op_income or 0) + (d_and_a or 0) if op_income is not None else None
    if gross_profit is None and revenue is not None and cogs is not None:
        gross_profit = revenue - cogs
    market_cap = shares * latest_price if (shares and latest_price) else None
    enterprise_value = (
        market_cap + total_debt - (cash or 0)
        if market_cap is not None
        else None
    )
    net_debt = total_debt - (cash or 0) if (lt_debt or st_debt or cash) else None

    def _ratio(n: float | None, d: float | None) -> float | None:
        if n is None or d is None or d == 0:
            return None
        return n / d

    def _growth(cur: float | None, prv: float | None) -> float | None:
        if cur is None or prv is None or prv == 0:
            return None
        return cur / prv - 1

    ratios: dict = {
        # Valuation
        "price": latest_price,
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "pe_ratio": _ratio(latest_price, eps_d),
        "pb_ratio": _ratio(market_cap, equity),
        "ps_ratio": _ratio(market_cap, revenue),
        "ev_ebitda": _ratio(enterprise_value, ebitda),
        "ev_revenue": _ratio(enterprise_value, revenue),
        "fcf_yield": _ratio(fcf, market_cap),
        "earnings_yield": _ratio(net_income, market_cap),
        # Profitability (margins)
        "gross_margin": _ratio(gross_profit, revenue),
        "operating_margin": _ratio(op_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        "roe": _ratio(net_income, equity),
        "roa": _ratio(net_income, assets),
        # Health
        "debt_to_equity": _ratio(total_debt, equity) if equity else None,
        "net_debt": net_debt,
        "debt_to_ebitda": _ratio(total_debt, ebitda),
        # Growth YoY
        "revenue_growth_yoy": _growth(revenue, prior_revenue),
        "net_income_growth_yoy": _growth(net_income, prior_net_income),
        # Raw (for context)
        "raw": {
            "revenue": revenue,
            "net_income": net_income,
            "eps_diluted": eps_d,
            "shares_outstanding": shares,
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "cash": cash,
            "total_debt": total_debt,
            "gross_profit": gross_profit,
            "operating_income": op_income,
            "ebitda": ebitda,
            "cfo": cfo,
            "capex": capex,
            "fcf": fcf,
        },
    }
    return {
        "ticker": ticker.upper(),
        "name": name,
        "cik": cik,
        "period": period.isoformat() if hasattr(period, "isoformat") else str(period),
        "fy": fy,
        "prior_period": prior[1].isoformat() if prior and hasattr(prior[1], "isoformat") else None,
        "ratios": ratios,
    }


def compute_ratios_snapshot(
    max_tickers: int | None = None,
    include_prices: bool = True,
) -> dict:
    """Compute a ratios snapshot across every ticker with a 10-K in the last 2y.

    Two passes for speed:
      1. Pull every ticker's XBRL-only ratios in bulk (one SQL query across all
         fsds_sub/fsds_num, no prices) — computes margins, ROE, D/E, growth, etc.
      2. Optionally batch-fetch latest prices (one yf.download call per 500
         tickers) and derive valuation ratios in memory.

    Storing to ratios_snapshot is atomic: DELETE + INSERT in a single connection.
    """
    con = connect()
    two_yrs_ago = (dt.date.today() - dt.timedelta(days=760)).isoformat()

    # Universe
    universe = con.execute(
        """
        SELECT tm.ticker, tm.cik, MAX(s.period) AS period, MAX(s.name) AS name,
               MAX(s.adsh) FILTER (WHERE s.period = (
                   SELECT MAX(period) FROM fsds_sub WHERE cik = tm.cik AND form = '10-K'
               )) AS latest_adsh
        FROM ticker_map tm
        JOIN fsds_sub s ON s.cik = tm.cik AND s.form = '10-K'
        WHERE s.period >= ?
        GROUP BY tm.ticker, tm.cik
        ORDER BY tm.ticker
        """,
        [two_yrs_ago],
    ).fetchall()
    if max_tickers:
        universe = universe[:max_tickers]
    if not universe:
        con.close()
        return {"total": 0, "stored": 0, "computed": 0, "price_ok": 0,
                "price_missing": 0, "errors": 0, "computed_at": dt.datetime.now().isoformat()}

    tickers = [u[0] for u in universe]
    adshes = [u[4] for u in universe if u[4]]

    # Pull every relevant num row for these filings in ONE query
    print(f"  pulling num rows for {len(adshes)} latest 10-Ks…")
    num_rows = con.execute(
        f"""
        SELECT n.adsh, n.tag, n.uom, n.qtrs, n.ddate, n.value
        FROM fsds_num n
        JOIN fsds_sub s USING (adsh)
        WHERE n.adsh IN ({','.join(['?'] * len(adshes))})
          AND (n.segments IS NULL OR n.segments = '')
          AND (n.coreg IS NULL OR n.coreg = '')
          AND n.uom IN ('USD', 'shares', 'USD/shares')
          AND (n.qtrs IS NULL OR n.qtrs IN (0, 4))
          AND n.ddate = s.period
        """,
        adshes,
    ).fetchall()
    print(f"  {len(num_rows)} rows. aggregating…")

    # Also get prior-year 10-K data for YoY growth
    prior_map: dict[int, str] = {}   # cik → prior adsh
    for cik in set(u[1] for u in universe):
        r = con.execute(
            """
            SELECT adsh FROM fsds_sub
            WHERE cik = ? AND form = '10-K' AND period < (
                SELECT MAX(period) FROM fsds_sub WHERE cik = ? AND form = '10-K'
            )
            ORDER BY period DESC LIMIT 1
            """,
            [cik, cik],
        ).fetchone()
        if r:
            prior_map[cik] = r[0]
    prior_adshes = list(prior_map.values())
    prior_num_rows: list[tuple] = []
    if prior_adshes:
        prior_num_rows = con.execute(
            f"""
            SELECT n.adsh, n.tag, n.value
            FROM fsds_num n
            JOIN fsds_sub s USING (adsh)
            WHERE n.adsh IN ({','.join(['?'] * len(prior_adshes))})
              AND (n.segments IS NULL OR n.segments = '')
              AND (n.coreg IS NULL OR n.coreg = '')
              AND n.uom IN ('USD', 'shares', 'USD/shares')
              AND (n.qtrs IS NULL OR n.qtrs IN (0, 4))
              AND n.ddate = s.period
            """,
            prior_adshes,
        ).fetchall()
    con.close()

    # Aggregate latest-filing tag values per adsh (take largest-abs as canonical)
    latest_by_adsh: dict[str, dict[str, float]] = {}
    for adsh, tag, uom, qtrs, ddate, value in num_rows:
        if value is None:
            continue
        d = latest_by_adsh.setdefault(adsh, {})
        ex = d.get(tag)
        if ex is None or abs(value) > abs(ex):
            d[tag] = value
    prior_by_adsh: dict[str, dict[str, float]] = {}
    for adsh, tag, value in prior_num_rows:
        if value is None:
            continue
        d = prior_by_adsh.setdefault(adsh, {})
        ex = d.get(tag)
        if ex is None or abs(value) > abs(ex):
            d[tag] = value

    # Optional batched price fetch
    price_by_ticker: dict[str, float | None] = {}
    if include_prices:
        price_by_ticker = _batch_latest_prices(tickers)

    # Build rows
    now = dt.datetime.now()
    insert_rows: list[tuple] = []
    stats = {"computed": 0, "price_ok": 0, "price_missing": 0, "errors": 0}
    for ticker, cik, period, name, adsh in universe:
        latest_vals = latest_by_adsh.get(adsh, {})
        prior_vals = prior_by_adsh.get(prior_map.get(cik, ""), {})
        if not latest_vals:
            stats["errors"] += 1
            continue
        revenue = _pick(latest_vals, _TAG_ALIASES["revenue"])
        prior_revenue = _pick(prior_vals, _TAG_ALIASES["revenue"])
        net_income = _pick(latest_vals, _TAG_ALIASES["net_income"])
        prior_ni = _pick(prior_vals, _TAG_ALIASES["net_income"])
        eps_d = (
            _pick(latest_vals, _TAG_ALIASES["eps_diluted"])
            or _pick(latest_vals, _TAG_ALIASES["eps_basic"])
        )
        shares = _pick(latest_vals, _TAG_ALIASES["shares_out"])
        assets = _pick(latest_vals, _TAG_ALIASES["assets"])
        equity = _pick(latest_vals, _TAG_ALIASES["equity"])
        cash = _pick(latest_vals, _TAG_ALIASES["cash"])
        lt_debt = _pick(latest_vals, _TAG_ALIASES["long_term_debt"]) or 0.0
        st_debt = _pick(latest_vals, _TAG_ALIASES["short_term_debt"]) or 0.0
        total_debt = (lt_debt or 0) + (st_debt or 0)
        gp = _pick(latest_vals, _TAG_ALIASES["gross_profit"])
        op_inc = _pick(latest_vals, _TAG_ALIASES["operating_income"])
        cogs = _pick(latest_vals, _TAG_ALIASES["cogs"])
        cfo = _pick(latest_vals, _TAG_ALIASES["cfo"])
        capex = _pick(latest_vals, _TAG_ALIASES["capex"])
        dna = _pick(latest_vals, _TAG_ALIASES["depreciation"])
        if gp is None and revenue is not None and cogs is not None:
            gp = revenue - cogs
        fcf = (cfo or 0) - abs(capex or 0) if cfo is not None else None
        ebitda = (op_inc or 0) + (dna or 0) if op_inc is not None else None

        price = price_by_ticker.get(ticker)
        if price is not None:
            stats["price_ok"] += 1
        else:
            stats["price_missing"] += 1
        mc = (shares * price) if (shares and price) else None
        ev = (mc + total_debt - (cash or 0)) if mc is not None else None

        def _div(n, d):
            if n is None or d is None or d == 0:
                return None
            return n / d

        insert_rows.append(
            (
                ticker,
                cik,
                name or "",
                period,
                price,
                mc,
                ev,
                _div(price, eps_d),
                _div(mc, equity),
                _div(mc, revenue),
                _div(ev, ebitda),
                _div(ev, revenue),
                _div(fcf, mc),
                _div(net_income, mc),
                _div(gp, revenue),
                _div(op_inc, revenue),
                _div(net_income, revenue),
                _div(net_income, equity),
                _div(net_income, assets),
                _div(total_debt, equity) if equity else None,
                _div(total_debt, ebitda),
                total_debt - (cash or 0) if (lt_debt or st_debt or cash) else None,
                _div(revenue - prior_revenue, prior_revenue)
                if revenue is not None and prior_revenue not in (None, 0)
                else None,
                _div(net_income - prior_ni, prior_ni)
                if net_income is not None and prior_ni not in (None, 0)
                else None,
                revenue,
                net_income,
                now,
            )
        )
        stats["computed"] += 1

    c2 = connect()
    c2.execute("DELETE FROM ratios_snapshot")
    if insert_rows:
        c2.executemany(
            "INSERT INTO ratios_snapshot VALUES (" + ",".join(["?"] * 27) + ")",
            insert_rows,
        )
    c2.close()
    stats["total"] = len(universe)
    stats["stored"] = len(insert_rows)
    stats["computed_at"] = now.isoformat()
    return stats


def _batch_latest_prices(tickers: list[str]) -> dict[str, float | None]:
    """Fetch most-recent close for a list of tickers in batches via yf.download.

    Best-effort: Yahoo rate limits will produce empty frames. We swallow and
    return {ticker: None} for those. Ratios downstream handle None gracefully.
    """
    import yfinance as yf
    out: dict[str, float | None] = {t: None for t in tickers}
    chunk = 200
    start = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    for i in range(0, len(tickers), chunk):
        batch = tickers[i : i + chunk]
        try:
            df = yf.download(
                batch, start=start, end=end, auto_adjust=False, actions=False,
                progress=False, threads=True, group_by="ticker",
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if len(batch) == 1:
            t = batch[0]
            if "Close" in df.columns and not df["Close"].empty:
                try:
                    out[t] = float(df["Close"].dropna().iloc[-1])
                except Exception:
                    pass
            continue
        for t in batch:
            try:
                s = df[(t, "Close")].dropna()
                if len(s):
                    out[t] = float(s.iloc[-1])
            except KeyError:
                pass
    return out


def screener_query(
    filters: dict,
    order_by: str = "market_cap",
    descending: bool = True,
    limit: int = 200,
) -> dict:
    """Query the ratios_snapshot with a dict of filter conditions."""
    allowed_cols = {
        "market_cap", "pe_ratio", "pb_ratio", "ps_ratio", "ev_ebitda", "ev_revenue",
        "fcf_yield", "earnings_yield", "gross_margin", "operating_margin", "net_margin",
        "roe", "roa", "debt_to_equity", "debt_to_ebitda", "revenue_growth_yoy",
        "net_income_growth_yoy", "revenue", "net_income",
        "quality_score", "magic_rank",  # composite scores computed below
    }
    # Composite scores computed inline — no schema change. Reduced-form
    # Piotroski: 5 of 9 tests using the data we have in ratios_snapshot.
    # Magic Formula approximation: sum of ranks on earnings_yield + roa.
    quality_expr = """
        (
            CAST(COALESCE(net_income, 0) > 0 AS INTEGER) +
            CAST(COALESCE(roa, 0) > 0 AS INTEGER) +
            CAST(COALESCE(revenue_growth_yoy, 0) > 0 AS INTEGER) +
            CAST(COALESCE(net_income_growth_yoy, 0) > 0 AS INTEGER) +
            CAST(COALESCE(debt_to_equity, 999) < 1 AS INTEGER)
        )
    """
    magic_expr = """
        (
            RANK() OVER (ORDER BY earnings_yield DESC NULLS LAST) +
            RANK() OVER (ORDER BY roa DESC NULLS LAST)
        )
    """
    clauses: list[str] = []
    params: list = []
    having_clauses: list[str] = []
    for key, val in (filters or {}).items():
        if val is None or val == "":
            continue
        # Boolean-ish: exclude ETF/trust/fund shells from the universe.
        # These report extreme ROAs from structured-product accounting and
        # pollute quality/magic rankings. Default ON feels right but we
        # leave it opt-in to avoid silently changing existing screens.
        if key == "exclude_etfs" and str(val).lower() in ("true", "1", "yes"):
            clauses.append(
                "upper(COALESCE(name,'')) NOT LIKE '%TRUST%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%ETF%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%FUND%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%PROSHARES%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%ISHARES%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%SPDR%' "
                "AND upper(COALESCE(name,'')) NOT LIKE '%ABRDN%'"
            )
            continue
        if key.endswith("_min"):
            col = key[:-4]
            if col in ("quality_score", "magic_rank"):
                having_clauses.append(f"{col} >= ?")
                params.append(float(val))
            elif col in allowed_cols:
                clauses.append(f"{col} >= ?")
                params.append(float(val))
        elif key.endswith("_max"):
            col = key[:-4]
            if col in ("quality_score", "magic_rank"):
                having_clauses.append(f"{col} <= ?")
                params.append(float(val))
            elif col in allowed_cols:
                clauses.append(f"{col} <= ?")
                params.append(float(val))
    if order_by not in allowed_cols | {"ticker", "name"}:
        order_by = "market_cap"
    # Magic rank: lower is better; flip direction on the frontend knob later.
    direction = "DESC" if descending else "ASC"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    # composite-score filters apply AFTER the CTE since they reference derived columns
    outer_where = ("WHERE " + " AND ".join(having_clauses)) if having_clauses else ""
    q = f"""
        WITH scored AS (
            SELECT *,
                   {quality_expr} AS quality_score,
                   {magic_expr}   AS magic_rank
            FROM ratios_snapshot
            {where}
        )
        SELECT * FROM scored
        {outer_where}
        ORDER BY {order_by} {direction} NULLS LAST
        LIMIT ?
    """
    con = connect()
    cols = [d[0] for d in con.execute(q + " ", params + [int(limit)]).description]
    rows = con.execute(q, params + [int(limit)]).fetchall()
    meta = con.execute(
        "SELECT COUNT(*) AS n, MAX(computed_at) AS ts FROM ratios_snapshot"
    ).fetchone()
    con.close()
    return {
        "rows": [
            {c: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v)
             for c, v in zip(cols, r)}
            for r in rows
        ],
        "columns": cols,
        "total": meta[0] if meta else 0,
        "computed_at": meta[1].isoformat() if meta and meta[1] else None,
    }


def _latest_price(ticker: str) -> float | None:
    """Get most recent close from yfinance (cached at provider level).

    Swallows RateLimited / network errors; caller is tolerant of None.
    """
    try:
        from providers import prices
        t = dt.date.today()
        start = (t - dt.timedelta(days=10)).isoformat()
        bars = prices.get_history(ticker, start, (t + dt.timedelta(days=1)).isoformat())
        if bars:
            return float(bars[-1]["close"])
    except Exception:
        pass
    return None


# ─── Form 144 ingest + query ────────────────────────────────────────────────

def _days_in_range(start: str, end: str) -> list[dt.date]:
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    out = []
    while s <= e:
        out.append(s)
        s += dt.timedelta(days=1)
    return out


def ensure_form144_window(start: str, end: str, *, max_days: int = 14) -> dict:
    """Scrape SEC for any un-ingested Form 144 days in [start, end].

    Caps backfill at `max_days` per call so a user hitting YTD doesn't kick off
    a 3-hour scrape synchronously; rest can be filled on subsequent calls.
    """
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute("SELECT day FROM _form144_ingested_days").fetchall()}
    missing = [d for d in all_days if d not in cached]
    # Prioritise recent days first (most interesting)
    missing.sort(reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}

    # Batch into contiguous ranges to minimise EFTS calls
    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_start = cur_end = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_end).days == 1:
            cur_end = d
        else:
            ranges.append((cur_start, cur_end))
            cur_start = cur_end = d
    ranges.append((cur_start, cur_end))

    # Import lazily so data_store has no top-level dep on providers
    from providers import form144 as f144

    ingested_days: list[str] = []
    for a, b in ranges:
        records = f144.fetch_window(a.isoformat(), b.isoformat())
        if records:
            con.executemany(
                """
                INSERT OR REPLACE INTO form144 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r.accession,
                        r.filing_date,
                        r.issuer_cik,
                        r.issuer_name,
                        r.seller_name,
                        r.relationship,
                        r.class_title,
                        r.shares,
                        r.aggregate_value,
                        r.approx_sale_date,
                        r.exchange,
                        r.broker_name,
                    )
                    for r in records
                ],
            )
        # Mark every day in this range as ingested, even if no filings landed
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _form144_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)

    remaining = len(missing) - len(to_do)
    con.close()
    return {
        "missing_days": len(missing),
        "ingested_days": ingested_days,
        "pending_days": remaining,
    }


def get_planned_sells(start: str, end: str, top: int = 25, min_value: float = 0) -> dict:
    """Aggregate Form 144 filings in [start, end] into a ticker leaderboard.

    Joins against ticker_map to surface tradable tickers. Filings without a
    matched ticker (private funds, BDCs, etc.) are kept but flagged.
    """
    con = connect()
    rows = con.execute(
        """
        SELECT f.accession, f.filing_date, f.issuer_cik, f.issuer_name,
               f.seller_name, f.relationship, f.class_title,
               f.shares, f.aggregate_value, f.approx_sale_date,
               f.exchange, f.broker_name,
               tm.ticker
        FROM form144 f
        LEFT JOIN ticker_map tm ON tm.cik = f.issuer_cik
        WHERE f.filing_date BETWEEN ? AND ?
          AND f.shares IS NOT NULL AND f.shares > 0
          AND f.aggregate_value IS NOT NULL AND f.aggregate_value > 0
          AND f.aggregate_value >= ?
        ORDER BY f.aggregate_value DESC
        """,
        [start, end, min_value],
    ).fetchall()
    cols = [
        "accession", "filing_date", "issuer_cik", "issuer_name",
        "seller_name", "relationship", "class_title", "shares",
        "aggregate_value", "approx_sale_date", "exchange", "broker_name", "ticker",
    ]
    transactions = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]

    # Aggregate by ticker (fallback to issuer_name if no ticker)
    per: dict[str, dict] = {}
    for tx in transactions:
        key = tx["ticker"] or f"—{(tx['issuer_name'] or '?')[:30]}"
        e = per.setdefault(
            key,
            {
                "ticker": tx["ticker"] or "",
                "issuer": (tx["issuer_name"] or "")[:40],
                "total_value": 0.0,
                "shares": 0.0,
                "sellers": set(),
                "positions": set(),
            },
        )
        e["total_value"] += tx["aggregate_value"] or 0.0
        e["shares"] += tx["shares"] or 0.0
        if tx["seller_name"]:
            e["sellers"].add(tx["seller_name"])
        if tx["relationship"]:
            e["positions"].add(tx["relationship"])

    leaderboard = []
    _c_suite = ("CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL", "PRESIDENT")
    for _, e in per.items():
        positions_joined = ", ".join(sorted(e["positions"]))[:80]
        csuite = "✓" if any(k in positions_joined.upper() for k in _c_suite) else ""
        leaderboard.append(
            {
                "ticker": e["ticker"] or "(private)",
                "issuer": e["issuer"],
                "total_$": f"${e['total_value']:,.0f}",
                "shares": f"{e['shares']:,.0f}",
                "# sellers": len(e["sellers"]),
                "C-suite": csuite,
                "positions": positions_joined,
                "value_num": e["total_value"],
            }
        )
    leaderboard.sort(key=lambda r: (-r["# sellers"], -r["value_num"]))
    leaderboard = leaderboard[:top]

    total_value = sum(t["aggregate_value"] or 0.0 for t in transactions)
    return {
        "summary": {
            "planned_sale_filings": len(transactions),
            "total_value": total_value,
            "cluster_tickers": sum(1 for r in leaderboard if r["# sellers"] >= 2),
            "csuite_tickers": sum(1 for r in leaderboard if r["C-suite"] == "✓"),
        },
        "leaderboard": leaderboard,
        "transactions": transactions,
    }


# ─── Schedule 13D ingest + query ────────────────────────────────────────────

def ensure_thirteen_d_window(start: str, end: str, *, max_days: int = 30) -> dict:
    """Scrape EFTS for any un-ingested 13D days in [start, end].

    EFTS is lightweight (no per-filing fetches), so we allow a wider daily cap
    than Form 144 (~30 days vs 14).
    """
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute("SELECT day FROM _thirteen_d_ingested_days").fetchall()}
    missing = [d for d in all_days if d not in cached]
    missing.sort(reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}

    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_start = cur_end = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_end).days == 1:
            cur_end = d
        else:
            ranges.append((cur_start, cur_end))
            cur_start = cur_end = d
    ranges.append((cur_start, cur_end))

    from providers import thirteen_d as t13

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = t13.fetch_window(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                """
                INSERT OR REPLACE INTO thirteen_d VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        f.accession,
                        f.form,
                        f.filing_date,
                        f.issuer_cik,
                        f.issuer_name,
                        f.issuer_ticker,
                        f.filer_cik,
                        f.filer_name,
                        f.primary_doc,
                    )
                    for f in filings
                ],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _thirteen_d_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {
        "missing_days": len(missing),
        "ingested_days": ingested_days,
        "pending_days": remaining,
    }


def get_activism_filings(start: str, end: str, top: int = 100) -> dict:
    """Return 13D filings in window + per-issuer and per-filer aggregations."""
    con = connect()
    rows = con.execute(
        """
        SELECT accession, form, filing_date, issuer_cik, issuer_name, issuer_ticker,
               filer_cik, filer_name, primary_doc
        FROM thirteen_d
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = [
        "accession", "form", "filing_date", "issuer_cik", "issuer_name", "issuer_ticker",
        "filer_cik", "filer_name", "primary_doc",
    ]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    con.close()

    # Per-issuer aggregation (what companies got the most 13D activity)
    by_issuer: dict[int | str, dict] = {}
    for f in filings:
        key = f["issuer_cik"] or f["issuer_name"]
        e = by_issuer.setdefault(
            key,
            {
                "issuer_name": f["issuer_name"],
                "issuer_ticker": f["issuer_ticker"],
                "issuer_cik": f["issuer_cik"],
                "n_filings": 0,
                "n_amendments": 0,
                "filers": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        if "/A" in (f["form"] or ""):
            e["n_amendments"] += 1
        if f["filer_name"]:
            e["filers"].add(f["filer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
    issuer_leaderboard = sorted(
        (
            {**e, "filers": sorted(e["filers"])[:5], "n_filers": len(e["filers"])}
            for e in by_issuer.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]

    # Per-filer aggregation (who's been most active)
    by_filer: dict[int | str, dict] = {}
    for f in filings:
        key = f["filer_cik"] or f["filer_name"]
        e = by_filer.setdefault(
            key,
            {
                "filer_name": f["filer_name"],
                "filer_cik": f["filer_cik"],
                "n_filings": 0,
                "targets": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        if f["issuer_name"]:
            e["targets"].add(f["issuer_ticker"] or f["issuer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
    filer_leaderboard = sorted(
        (
            {**e, "targets": sorted(e["targets"])[:8], "n_targets": len(e["targets"])}
            for e in by_filer.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]

    return {
        "summary": {
            "total_filings": len(filings),
            "amendments": sum(1 for f in filings if "/A" in (f["form"] or "")),
            "unique_issuers": len(by_issuer),
            "unique_filers": len(by_filer),
        },
        "filings": filings,
        "by_issuer": issuer_leaderboard,
        "by_filer": filer_leaderboard,
    }


# ─── SC 13G / Form 3 / Form D — shared ingest pattern ─────────────────────

def _ensure_generic_window(
    table: str,
    ingested_table: str,
    fetch_fn,
    start: str,
    end: str,
    max_days: int,
) -> dict:
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute(f"SELECT day FROM {ingested_table}").fetchall()}
    missing = sorted([d for d in all_days if d not in cached], reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}
    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_s = cur_e = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_e).days == 1:
            cur_e = d
        else:
            ranges.append((cur_s, cur_e))
            cur_s = cur_e = d
    ranges.append((cur_s, cur_e))

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = fetch_fn(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (f.accession, f.form, f.filing_date, f.issuer_cik, f.issuer_name,
                     f.issuer_ticker, f.filer_cik, f.filer_name, f.primary_doc)
                    for f in filings
                ],
            )
        d = a
        while d <= b:
            con.execute(
                f"INSERT OR REPLACE INTO {ingested_table} VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {"missing_days": len(missing), "ingested_days": ingested_days, "pending_days": remaining}


def _get_generic_by_issuer(table: str, start: str, end: str, top: int = 300) -> dict:
    con = connect()
    rows = con.execute(
        f"""
        SELECT accession, form, filing_date, issuer_cik, issuer_name, issuer_ticker,
               filer_cik, filer_name, primary_doc
        FROM {table}
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = ["accession", "form", "filing_date", "issuer_cik", "issuer_name", "issuer_ticker",
            "filer_cik", "filer_name", "primary_doc"]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    by_issuer: dict[int | str, dict] = {}
    form_counts: dict[str, int] = {}
    for f in filings:
        key = f["issuer_cik"] or f["issuer_name"]
        e = by_issuer.setdefault(
            key,
            {
                "issuer_name": f["issuer_name"],
                "issuer_ticker": f["issuer_ticker"],
                "issuer_cik": f["issuer_cik"],
                "n_filings": 0,
                "forms": set(),
                "filers": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["forms"].add(f["form"])
        if f["filer_name"]:
            e["filers"].add(f["filer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
        form_counts[f["form"]] = form_counts.get(f["form"], 0) + 1
    issuer_leaderboard = sorted(
        (
            {**e, "forms": sorted(e["forms"]), "filers": sorted(e["filers"])[:5], "n_filers": len(e["filers"])}
            for e in by_issuer.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]
    con.close()
    return {
        "summary": {
            "total_filings": len(filings),
            "unique_issuers": len(by_issuer),
            "form_counts": form_counts,
        },
        "filings": filings,
        "by_issuer": issuer_leaderboard,
    }


def ensure_13g_window(start: str, end: str, *, max_days: int = 45) -> dict:
    from providers.simple_filings import fetch_13g
    return _ensure_generic_window("schedule_13g", "_schedule_13g_ingested_days",
                                   fetch_13g, start, end, max_days)


def get_13g_filings(start: str, end: str, top: int = 300) -> dict:
    return _get_generic_by_issuer("schedule_13g", start, end, top)


def ensure_form3_window(start: str, end: str, *, max_days: int = 30) -> dict:
    from providers.simple_filings import fetch_form3
    return _ensure_generic_window("form3_initials", "_form3_ingested_days",
                                   fetch_form3, start, end, max_days)


def get_form3_filings(start: str, end: str, top: int = 300) -> dict:
    return _get_generic_by_issuer("form3_initials", start, end, top)


def ensure_formD_window(start: str, end: str, *, max_days: int = 30) -> dict:
    from providers.simple_filings import fetch_formD
    return _ensure_generic_window("form_d", "_form_d_ingested_days",
                                   fetch_formD, start, end, max_days)


def get_formD_filings(start: str, end: str, top: int = 300) -> dict:
    return _get_generic_by_issuer("form_d", start, end, top)


def ensure_delistings_window(start: str, end: str, *, max_days: int = 60) -> dict:
    from providers.simple_filings import fetch_delistings
    return _ensure_generic_window("delistings", "_delistings_ingested_days",
                                   fetch_delistings, start, end, max_days)


def get_delistings(start: str, end: str, top: int = 300) -> dict:
    return _get_generic_by_issuer("delistings", start, end, top)


def ensure_foreign_filings_window(start: str, end: str, *, max_days: int = 14) -> dict:
    """6-K volume is huge (~8k/month), so keep per-call backfill narrow."""
    from providers.simple_filings import fetch_foreign_issuers
    return _ensure_generic_window("foreign_filings", "_foreign_filings_ingested_days",
                                   fetch_foreign_issuers, start, end, max_days)


def get_foreign_filings(start: str, end: str, top: int = 300) -> dict:
    return _get_generic_by_issuer("foreign_filings", start, end, top)


# ─── Secondary offerings ingest + query ────────────────────────────────────

def ensure_secondaries_window(start: str, end: str, *, max_days: int = 45) -> dict:
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute(
        "SELECT day FROM _secondaries_ingested_days"
    ).fetchall()}
    missing = sorted([d for d in all_days if d not in cached], reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}
    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_s = cur_e = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_e).days == 1:
            cur_e = d
        else:
            ranges.append((cur_s, cur_e))
            cur_s = cur_e = d
    ranges.append((cur_s, cur_e))

    from providers import secondaries as sec

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = sec.fetch_window(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                "INSERT OR REPLACE INTO secondaries VALUES (?,?,?,?,?,?,?)",
                [(f.accession, f.form, f.filing_date, f.issuer_cik,
                  f.issuer_name, f.issuer_ticker, f.primary_doc) for f in filings],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _secondaries_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {"missing_days": len(missing), "ingested_days": ingested_days, "pending_days": remaining}


def get_secondaries(start: str, end: str, top: int = 300) -> dict:
    con = connect()
    rows = con.execute(
        """
        SELECT accession, form, filing_date, issuer_cik, issuer_name, issuer_ticker, primary_doc
        FROM secondaries
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = ["accession", "form", "filing_date", "issuer_cik", "issuer_name", "issuer_ticker", "primary_doc"]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    by_issuer: dict[int | str, dict] = {}
    form_counts: dict[str, int] = {}
    for f in filings:
        key = f["issuer_cik"] or f["issuer_name"]
        e = by_issuer.setdefault(
            key,
            {
                "issuer_name": f["issuer_name"],
                "issuer_ticker": f["issuer_ticker"],
                "issuer_cik": f["issuer_cik"],
                "n_filings": 0,
                "forms": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["forms"].add(f["form"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
        form_counts[f["form"]] = form_counts.get(f["form"], 0) + 1
    issuer_leaderboard = sorted(
        ({**e, "forms": sorted(e["forms"])} for e in by_issuer.values()),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]
    con.close()
    return {
        "summary": {
            "total_filings": len(filings),
            "unique_issuers": len(by_issuer),
            "form_counts": form_counts,
            "repeat_issuers": sum(1 for e in by_issuer.values() if e["n_filings"] > 1),
        },
        "filings": filings,
        "by_issuer": issuer_leaderboard,
    }


# ─── Tender offers ingest + query ──────────────────────────────────────────

def ensure_tender_offers_window(start: str, end: str, *, max_days: int = 60) -> dict:
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute(
        "SELECT day FROM _tender_offers_ingested_days"
    ).fetchall()}
    missing = sorted([d for d in all_days if d not in cached], reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}
    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_s = cur_e = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_e).days == 1:
            cur_e = d
        else:
            ranges.append((cur_s, cur_e))
            cur_s = cur_e = d
    ranges.append((cur_s, cur_e))

    from providers import tender_offers as tof

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = tof.fetch_window(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                "INSERT OR REPLACE INTO tender_offers VALUES (?,?,?,?,?,?,?,?,?)",
                [(f.accession, f.form, f.filing_date, f.subject_cik, f.subject_name,
                  f.subject_ticker, f.filer_cik, f.filer_name, f.primary_doc) for f in filings],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _tender_offers_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {"missing_days": len(missing), "ingested_days": ingested_days, "pending_days": remaining}


def get_tender_offers(start: str, end: str, top: int = 300) -> dict:
    con = connect()
    rows = con.execute(
        """
        SELECT accession, form, filing_date, subject_cik, subject_name,
               subject_ticker, filer_cik, filer_name, primary_doc
        FROM tender_offers
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = ["accession", "form", "filing_date", "subject_cik", "subject_name",
            "subject_ticker", "filer_cik", "filer_name", "primary_doc"]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    by_target: dict[int | str, dict] = {}
    form_counts: dict[str, int] = {}
    for f in filings:
        key = f["subject_cik"] or f["subject_name"]
        e = by_target.setdefault(
            key,
            {
                "subject_name": f["subject_name"],
                "subject_ticker": f["subject_ticker"],
                "subject_cik": f["subject_cik"],
                "n_filings": 0,
                "forms": set(),
                "acquirers": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["forms"].add(f["form"])
        if f["filer_name"] and f["filer_name"] != f["subject_name"]:
            e["acquirers"].add(f["filer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
        form_counts[f["form"]] = form_counts.get(f["form"], 0) + 1
    target_leaderboard = sorted(
        (
            {**e, "forms": sorted(e["forms"]), "acquirers": sorted(e["acquirers"])[:3]}
            for e in by_target.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]
    con.close()
    return {
        "summary": {
            "total_filings": len(filings),
            "unique_targets": len(by_target),
            "form_counts": form_counts,
        },
        "filings": filings,
        "by_target": target_leaderboard,
    }


# ─── Proxy contests ingest + query ─────────────────────────────────────────

def ensure_proxy_contests_window(start: str, end: str, *, max_days: int = 45) -> dict:
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute(
        "SELECT day FROM _proxy_contests_ingested_days"
    ).fetchall()}
    missing = sorted([d for d in all_days if d not in cached], reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}

    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_start = cur_end = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_end).days == 1:
            cur_end = d
        else:
            ranges.append((cur_start, cur_end))
            cur_start = cur_end = d
    ranges.append((cur_start, cur_end))

    from providers import proxy_contests as pc

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = pc.fetch_window(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                "INSERT OR REPLACE INTO proxy_contests VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f.accession,
                        f.form,
                        f.filing_date,
                        f.issuer_cik,
                        f.issuer_name,
                        f.issuer_ticker,
                        f.filer_cik,
                        f.filer_name,
                        f.primary_doc,
                    )
                    for f in filings
                ],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _proxy_contests_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {
        "missing_days": len(missing),
        "ingested_days": ingested_days,
        "pending_days": remaining,
    }


def get_proxy_contests(start: str, end: str, top: int = 300) -> dict:
    con = connect()
    rows = con.execute(
        """
        SELECT accession, form, filing_date, issuer_cik, issuer_name,
               issuer_ticker, filer_cik, filer_name, primary_doc
        FROM proxy_contests
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = [
        "accession", "form", "filing_date", "issuer_cik", "issuer_name",
        "issuer_ticker", "filer_cik", "filer_name", "primary_doc",
    ]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    # Per-issuer: companies with the most activist proxy activity
    by_issuer: dict[int | str, dict] = {}
    form_counts: dict[str, int] = {}
    for f in filings:
        key = f["issuer_cik"] or f["issuer_name"]
        e = by_issuer.setdefault(
            key,
            {
                "issuer_name": f["issuer_name"],
                "issuer_ticker": f["issuer_ticker"],
                "issuer_cik": f["issuer_cik"],
                "n_filings": 0,
                "forms": set(),
                "filers": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["forms"].add(f["form"])
        if f["filer_name"]:
            e["filers"].add(f["filer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
        form_counts[f["form"]] = form_counts.get(f["form"], 0) + 1
    issuer_leaderboard = sorted(
        (
            {**e, "forms": sorted(e["forms"]), "filers": sorted(e["filers"])[:5], "n_filers": len(e["filers"])}
            for e in by_issuer.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]

    # Per-filer: who's been active
    by_filer: dict[int | str, dict] = {}
    for f in filings:
        key = f["filer_cik"] or f["filer_name"]
        if not f["filer_name"]:
            continue
        e = by_filer.setdefault(
            key,
            {
                "filer_name": f["filer_name"],
                "filer_cik": f["filer_cik"],
                "n_filings": 0,
                "targets": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        if f["issuer_name"]:
            e["targets"].add(f["issuer_ticker"] or f["issuer_name"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
    filer_leaderboard = sorted(
        (
            {**e, "targets": sorted(e["targets"])[:8], "n_targets": len(e["targets"])}
            for e in by_filer.values()
        ),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]

    con.close()
    return {
        "summary": {
            "total_filings": len(filings),
            "unique_issuers": len(by_issuer),
            "unique_filers": len(by_filer),
            "form_counts": form_counts,
        },
        "filings": filings,
        "by_issuer": issuer_leaderboard,
        "by_filer": filer_leaderboard,
    }


# ─── 13F superinvestor holdings ingest + query ─────────────────────────────

def seed_fund_meta() -> int:
    """Populate fund_meta with the curated starter list if empty."""
    from providers.form_13f import CURATED_FUNDS

    con = connect()
    existing = con.execute("SELECT COUNT(*) FROM fund_meta").fetchone()[0]
    if existing > 0:
        con.close()
        return existing
    con.executemany(
        "INSERT OR REPLACE INTO fund_meta VALUES (?,?,?,?)",
        [(cik, label, long_name, dt.datetime.now()) for cik, label, long_name in CURATED_FUNDS],
    )
    con.close()
    return len(CURATED_FUNDS)


def list_funds() -> list[dict]:
    con = connect()
    rows = con.execute(
        "SELECT cik, label, long_name FROM fund_meta ORDER BY label"
    ).fetchall()
    con.close()
    return [{"cik": c, "label": l, "long_name": n} for c, l, n in rows]


def ingest_fund_13f(cik: int, max_filings: int = 6, *, force: bool = False) -> dict:
    """Pull the last N 13F filings for a given fund CIK into fund_holdings.

    Aggregates rows with the same (cusip, title, put_call) since sub-manager
    splits produce duplicate entries per position.
    """
    from providers.form_13f import fetch_positions, list_13f_filings

    filings = list_13f_filings(cik, max_filings=max_filings)
    con = connect()
    if not force:
        ingested = {
            p for (p,) in con.execute(
                "SELECT period_end FROM _fund_ingested WHERE filer_cik = ?", [cik]
            ).fetchall()
        }
    else:
        ingested = set()
    new_periods: list[str] = []
    cached_periods: list[str] = []
    for f in filings:
        try:
            per = dt.date.fromisoformat(f.period_end) if f.period_end else None
        except ValueError:
            per = None
        if per is None:
            continue
        if per in ingested and not force:
            cached_periods.append(f.period_end)
            continue
        positions = fetch_positions(cik, f.accession)
        # Aggregate by (cusip, title, put_call)
        agg: dict[tuple[str, str, str], dict] = {}
        for p in positions:
            key = (p.cusip, p.title_of_class, p.put_call or "")
            e = agg.setdefault(key, {"issuer_name": p.issuer_name, "value": 0.0, "shares": 0.0})
            e["value"] += p.value_usd
            e["shares"] += p.shares
            if not e["issuer_name"]:
                e["issuer_name"] = p.issuer_name
        # Replace prior holdings for this (cik, period)
        con.execute(
            "DELETE FROM fund_holdings WHERE filer_cik = ? AND period_end = ?",
            [cik, per],
        )
        rows = [
            (
                cik,
                per,
                f.accession,
                cusip,
                e["issuer_name"],
                title,
                e["value"],
                e["shares"],
                put_call,
            )
            for (cusip, title, put_call), e in agg.items()
        ]
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO fund_holdings VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
        con.execute(
            "INSERT OR REPLACE INTO _fund_ingested VALUES (?, ?, ?)",
            [cik, per, dt.datetime.now()],
        )
        new_periods.append(f.period_end)
    con.close()
    return {"cik": cik, "newly_ingested": new_periods, "cached": cached_periods}


def ingest_all_funds(max_filings: int = 6) -> dict:
    """Iterate every fund in fund_meta and pull holdings."""
    seed_fund_meta()
    con = connect()
    ciks = [r[0] for r in con.execute("SELECT cik FROM fund_meta").fetchall()]
    con.close()
    results = []
    for cik in ciks:
        try:
            results.append(ingest_fund_13f(cik, max_filings=max_filings))
        except Exception as e:
            print(f"  ! fund {cik}: {type(e).__name__}: {e}")
            results.append({"cik": cik, "error": str(e)})
    return {"funds": results}


_ABBREV_EXPANSIONS = {
    "HLDG": "HOLDING",
    "HLDGS": "HOLDINGS",
    "HLDS": "HOLDINGS",
    "GRP": "GROUP",
    "TECH": "TECHNOLOGY",
    "COMMS": "COMMUNICATIONS",
    "PHARM": "PHARMACEUTICAL",
    "PHARMA": "PHARMACEUTICAL",
    "PETE": "PETROLEUM",
    "FINL": "FINANCIAL",
    "FINCL": "FINANCIAL",
    "FINCLS": "FINANCIAL",
    "INTL": "INTERNATIONAL",
    "INDS": "INDUSTRIES",
    "IND": "INDUSTRIES",
    "SVCS": "SERVICES",
    "SVC": "SERVICES",
    "SYS": "SYSTEMS",
    "RLTY": "REALTY",
    "RES": "RESOURCES",
    "INCORPORATED": "INC",
    "CORPORATION": "CORP",
    "LIMITED": "LTD",
    "COMPANY": "CO",
}

_DROP_SUFFIX_TOKENS = {
    "INC", "CORP", "CO", "LTD", "PLC",
    "LP", "LLC", "LLP", "HOLDINGS", "HOLDING", "GROUP", "TRUST", "FUND",
    "CLASS", "CL", "COM", "COMMON", "STOCK", "ORD", "ORDINARY", "ADR",
    "SA", "NV", "AG", "SE", "NPV",
    "A", "B", "C", "I", "II", "III", "IV", "V", "THE",
    # filler connectors that 13F transcripts often drop
    "OF", "AND", "&",
    # SEC filer naming quirks
    "NEW", "OLD", "DELAWARE",
}


def _normalize_issuer_name(name: str) -> str:
    """Produce a comparable key from an issuer name.

    Strategy: uppercase → punctuation → space → expand abbreviations →
    tokenize → drop corporate-suffix tokens → rejoin.
    """
    if not name:
        return ""
    import re as _re
    # Strip SEC state-of-incorporation markers like /DE/, /CA/, /CAN/, /MD/
    s = _re.sub(r"/[A-Z]{2,3}/?", " ", name)
    # Strip apostrophes outright so "Moody's" stays one token, not two.
    s = s.replace("'", "").replace("’", "")
    # All other punctuation becomes a space separator.
    s = _re.sub(r"[^A-Za-z0-9]+", " ", s).upper().strip()
    tokens = s.split()
    out: list[str] = []
    for tok in tokens:
        expanded = _ABBREV_EXPANSIONS.get(tok, tok)
        if expanded in _DROP_SUFFIX_TOKENS:
            continue
        out.append(expanded)
    return " ".join(out)


def _build_name_to_ticker_index(con) -> dict[str, str]:
    """Build a normalized-name → ticker dict from ticker_map. Called once per query."""
    rows = con.execute("SELECT ticker, name FROM ticker_map").fetchall()
    idx: dict[str, str] = {}
    # Preserve insertion order so earlier tickers win ties (ticker_map is
    # lexicographically sorted, so shorter tickers tend to come first)
    for ticker, name in rows:
        key = _normalize_issuer_name(name)
        if key and key not in idx:
            idx[key] = ticker
    return idx


def get_fund_latest_holdings(cik: int, top: int = 50) -> dict:
    con = connect()
    meta = con.execute(
        "SELECT label, long_name FROM fund_meta WHERE cik = ?", [cik]
    ).fetchone()
    latest_period = con.execute(
        "SELECT MAX(period_end) FROM fund_holdings WHERE filer_cik = ?", [cik]
    ).fetchone()[0]
    prior_period = con.execute(
        "SELECT MAX(period_end) FROM fund_holdings WHERE filer_cik = ? AND period_end < ?",
        [cik, latest_period],
    ).fetchone()[0] if latest_period else None

    if latest_period is None:
        con.close()
        return {"cik": cik, "error": "no holdings ingested"}

    name_to_ticker = _build_name_to_ticker_index(con)

    latest = con.execute(
        """
        SELECT cusip, issuer_name, title_of_class, put_call, value_usd, shares
        FROM fund_holdings
        WHERE filer_cik = ? AND period_end = ?
        ORDER BY value_usd DESC
        """,
        [cik, latest_period],
    ).fetchall()
    # Attach ticker via normalized-name lookup
    def _ticker_for(name: str) -> str | None:
        key = _normalize_issuer_name(name)
        return name_to_ticker.get(key)

    latest = [(*row, _ticker_for(row[1])) for row in latest]

    # Deltas vs prior period
    prior_map: dict[tuple[str, str, str], tuple[float, float]] = {}
    if prior_period:
        for cusip, title, pc, val, shr in con.execute(
            """
            SELECT cusip, title_of_class, put_call, value_usd, shares
            FROM fund_holdings WHERE filer_cik = ? AND period_end = ?
            """,
            [cik, prior_period],
        ).fetchall():
            prior_map[(cusip, title, pc or "")] = (val, shr)
    con.close()

    def _classify(cusip, title, pc, shares, prior_shares):
        if prior_shares == 0:
            return "NEW"
        if shares > prior_shares * 1.25:
            return "LOAD"   # +25% or more
        if shares < prior_shares * 0.75 and shares > 0:
            return "TRIM"   # -25% or more
        if shares == 0 and prior_shares > 0:
            return "EXIT"
        return "HOLD"

    positions = []
    for cusip, name, title, pc, val, shr, ticker in latest[:top * 3]:  # enough to sort
        prior_val, prior_shr = prior_map.get((cusip, title, pc or ""), (0.0, 0.0))
        delta = _classify(cusip, title, pc or "", shr, prior_shr)
        positions.append(
            {
                "cusip": cusip,
                "issuer_name": name,
                "title_of_class": title,
                "put_call": pc or "",
                "value_usd": val,
                "shares": shr,
                "prior_value": prior_val,
                "prior_shares": prior_shr,
                "shares_change": shr - prior_shr,
                "value_change": val - prior_val,
                "delta": delta,
                "ticker": ticker,
            }
        )
    # Also compute sold-out positions (in prior, not in latest)
    exits = []
    latest_keys = {(p["cusip"], p["title_of_class"], p["put_call"]) for p in positions}
    for (cusip, title, pc), (pval, pshr) in prior_map.items():
        if (cusip, title, pc) not in latest_keys and pshr > 0:
            exits.append(
                {
                    "cusip": cusip,
                    "title_of_class": title,
                    "put_call": pc,
                    "prior_value": pval,
                    "prior_shares": pshr,
                    "delta": "EXIT",
                }
            )

    return {
        "cik": cik,
        "label": meta[0] if meta else None,
        "long_name": meta[1] if meta else None,
        "latest_period": str(latest_period),
        "prior_period": str(prior_period) if prior_period else None,
        "n_positions": len(positions),
        "total_value": sum(p["value_usd"] for p in positions),
        "holdings": positions[:top],
        "new_positions": [p for p in positions if p["delta"] == "NEW"][:top],
        "loads": [p for p in positions if p["delta"] == "LOAD"][:top],
        "trims": [p for p in positions if p["delta"] == "TRIM"][:top],
        "exits": exits[:top],
    }


# ─── NT 10-K / NT 10-Q late filings ingest + query ─────────────────────────

def ensure_late_filings_window(start: str, end: str, *, max_days: int = 60) -> dict:
    """Scrape any un-ingested NT-filing days in [start, end].

    Low filing volume (~50-200 per week) means we can afford a larger per-call
    backfill cap than the 8-K or Form 144 trackers.
    """
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute("SELECT day FROM _late_filings_ingested_days").fetchall()}
    missing = [d for d in all_days if d not in cached]
    missing.sort(reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}

    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_start = cur_end = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_end).days == 1:
            cur_end = d
        else:
            ranges.append((cur_start, cur_end))
            cur_start = cur_end = d
    ranges.append((cur_start, cur_end))

    from providers import late_filings as lf

    ingested_days: list[str] = []
    failed_days: list[str] = []
    for a, b in ranges:
        try:
            filings = lf.fetch_window(a.isoformat(), b.isoformat())
        except lf.EftsError as e:
            print(f"  skipping {a} → {b}: {e}")
            d = a
            while d <= b:
                failed_days.append(d.isoformat())
                d += dt.timedelta(days=1)
            continue
        if filings:
            con.executemany(
                "INSERT OR REPLACE INTO late_filings VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        f.accession,
                        f.form,
                        f.filing_date,
                        f.issuer_cik,
                        f.issuer_name,
                        f.issuer_ticker,
                        f.primary_doc,
                    )
                    for f in filings
                ],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _late_filings_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {
        "missing_days": len(missing),
        "ingested_days": ingested_days,
        "pending_days": remaining,
    }


def get_late_filings(start: str, end: str, top: int = 300) -> dict:
    """List NT-filings in window plus per-issuer counts (repeat offenders)."""
    con = connect()
    rows = con.execute(
        """
        SELECT accession, form, filing_date, issuer_cik, issuer_name, issuer_ticker, primary_doc
        FROM late_filings
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    cols = ["accession", "form", "filing_date", "issuer_cik", "issuer_name", "issuer_ticker", "primary_doc"]
    filings = [
        {c: (v.isoformat() if isinstance(v, dt.date) else v) for c, v in zip(cols, r)}
        for r in rows
    ]
    by_issuer: dict[int | str, dict] = {}
    form_counts: dict[str, int] = {}
    for f in filings:
        key = f["issuer_cik"] or f["issuer_name"]
        e = by_issuer.setdefault(
            key,
            {
                "issuer_name": f["issuer_name"],
                "issuer_ticker": f["issuer_ticker"],
                "issuer_cik": f["issuer_cik"],
                "n_filings": 0,
                "forms": set(),
                "latest": f["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["forms"].add(f["form"])
        if f["filing_date"] > e["latest"]:
            e["latest"] = f["filing_date"]
        form_counts[f["form"]] = form_counts.get(f["form"], 0) + 1
    issuer_leaderboard = sorted(
        ({**e, "forms": sorted(e["forms"])} for e in by_issuer.values()),
        key=lambda r: (-r["n_filings"], r["latest"]),
    )[:top]
    con.close()
    return {
        "summary": {
            "total_filings": len(filings),
            "unique_issuers": len(by_issuer),
            "form_counts": form_counts,
            "repeat_issuers": sum(1 for e in by_issuer.values() if e["n_filings"] > 1),
        },
        "filings": filings,
        "by_issuer": issuer_leaderboard,
    }


# ─── 8-K material events ingest + query ────────────────────────────────────

def ensure_8k_window(start: str, end: str, *, max_days: int = 30) -> dict:
    """Scrape any un-ingested 8-K days in [start, end]."""
    con = connect()
    all_days = _days_in_range(start, end)
    cached = {row[0] for row in con.execute("SELECT day FROM _form_8k_ingested_days").fetchall()}
    missing = [d for d in all_days if d not in cached]
    missing.sort(reverse=True)
    to_do = missing[:max_days]
    if not to_do:
        con.close()
        return {"missing_days": 0, "ingested_days": [], "pending_days": 0}

    to_do_sorted = sorted(to_do)
    ranges: list[tuple[dt.date, dt.date]] = []
    cur_start = cur_end = to_do_sorted[0]
    for d in to_do_sorted[1:]:
        if (d - cur_end).days == 1:
            cur_end = d
        else:
            ranges.append((cur_start, cur_end))
            cur_start = cur_end = d
    ranges.append((cur_start, cur_end))

    from providers import form_8k as f8k

    ingested_days: list[str] = []
    for a, b in ranges:
        filings = f8k.fetch_window(a.isoformat(), b.isoformat())
        if filings:
            con.executemany(
                "INSERT OR REPLACE INTO form_8k VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        f.accession,
                        f.filing_date,
                        f.issuer_cik,
                        f.issuer_name,
                        f.issuer_ticker,
                        ",".join(f.items),
                        f.primary_doc,
                    )
                    for f in filings
                ],
            )
        d = a
        while d <= b:
            con.execute(
                "INSERT OR REPLACE INTO _form_8k_ingested_days VALUES (?, ?)",
                [d, dt.datetime.now()],
            )
            ingested_days.append(d.isoformat())
            d += dt.timedelta(days=1)
    remaining = len(missing) - len(to_do)
    con.close()
    return {
        "missing_days": len(missing),
        "ingested_days": ingested_days,
        "pending_days": remaining,
    }


def get_8k_events(
    start: str,
    end: str,
    items_filter: list[str] | None = None,
    top: int = 200,
) -> dict:
    """Return 8-K filings filtered by item codes, with per-item + per-issuer aggregations."""
    con = connect()
    rows = con.execute(
        """
        SELECT accession, filing_date, issuer_cik, issuer_name, issuer_ticker, items, primary_doc
        FROM form_8k
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY filing_date DESC
        """,
        [start, end],
    ).fetchall()
    con.close()

    # Filter in Python (items is comma-separated, want any-intersect semantics)
    wanted = set(items_filter) if items_filter else None
    filings: list[dict] = []
    per_item_counts: dict[str, int] = {}
    per_issuer: dict[int | str, dict] = {}
    for acc, fd, cik, name, ticker, items_str, pdoc in rows:
        items = [i for i in (items_str or "").split(",") if i]
        if wanted is not None and not any(i in wanted for i in items):
            continue
        filings.append(
            {
                "accession": acc,
                "filing_date": fd.isoformat() if hasattr(fd, "isoformat") else str(fd),
                "issuer_cik": cik,
                "issuer_name": name,
                "issuer_ticker": ticker,
                "items": items,
                "primary_doc": pdoc,
            }
        )
        for i in items:
            per_item_counts[i] = per_item_counts.get(i, 0) + 1
        key = cik or name
        e = per_issuer.setdefault(
            key,
            {
                "issuer_name": name,
                "issuer_ticker": ticker,
                "issuer_cik": cik,
                "n_filings": 0,
                "items_seen": set(),
                "latest": filings[-1]["filing_date"],
            },
        )
        e["n_filings"] += 1
        e["items_seen"].update(items)
        if filings[-1]["filing_date"] > e["latest"]:
            e["latest"] = filings[-1]["filing_date"]

    issuer_leaderboard = sorted(
        (
            {**e, "items_seen": sorted(e["items_seen"])}
            for e in per_issuer.values()
        ),
        key=lambda r: -r["n_filings"],
    )[:top]

    return {
        "summary": {
            "total_filings": len(filings),
            "unique_issuers": len(per_issuer),
            "items_active": len(per_item_counts),
        },
        "item_counts": dict(sorted(per_item_counts.items())),
        "filings": filings[:top * 5],  # more rows than leaderboard to populate the feed
        "by_issuer": issuer_leaderboard,
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", nargs=2, metavar=("YEAR", "QUARTER"), type=int)
    ap.add_argument("--query", nargs=2, metavar=("START", "END"))
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    if args.ingest:
        did = ingest_insider_quarter(Quarter(args.ingest[0], args.ingest[1]))
        print(f"ingested={did}")
    if args.query:
        out = get_insider_purchases(args.query[0], args.query[1], top=args.top)
        out["transactions"] = out["transactions"][:10]  # trim for readability
        print(json.dumps(out, default=str, indent=2))

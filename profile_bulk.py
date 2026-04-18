"""Profile approaches for Form 4 insider-purchase data.

Baseline (A) is the existing live-scrape pipeline. B/C/D use SEC's pre-parsed
DERA Insider Transactions Data Set — one quarterly ZIP instead of N filings.

Task every approach must produce:
  Insider open-market purchases (code 'P') for [START, END], grouped by ticker,
  ordered by total $ value desc.
"""
from __future__ import annotations

import io
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

HEADERS = {"User-Agent": "wingatejones@gmail.com"}
CACHE_DIR = Path(__file__).parent / ".cache" / "dera"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Window to screen — pick something inside a released DERA quarter.
# Q1 2026 is released; Q2 (Apr-Jun) is not yet. Profile against Q1 dates.
START = "2026-03-01"
END = "2026-03-15"
YEAR, QUARTER = 2026, 1

CANDIDATE_URLS = [
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip",
]


def download_quarter(year: int, quarter: int) -> Path:
    """Download the DERA Insider Transactions ZIP for a quarter, cached on disk."""
    dest = CACHE_DIR / f"insider_{year}q{quarter}.zip"
    if dest.exists():
        return dest
    last_err: Exception | None = None
    for tmpl in CANDIDATE_URLS:
        url = tmpl.format(y=year, q=quarter)
        try:
            r = httpx.get(url, headers=HEADERS, timeout=120, follow_redirects=True)
            if r.status_code == 200 and r.content[:2] == b"PK":
                dest.write_bytes(r.content)
                print(f"  downloaded {url} → {dest} ({len(r.content)/1e6:.1f} MB)")
                return dest
            last_err = RuntimeError(f"{url} → {r.status_code}")
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Could not locate DERA ZIP for {year}Q{quarter}: {last_err}")


def list_zip_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


# ─── A: Live scrape (existing pipeline) ─────────────────────────────────────
def approach_a_live() -> tuple[list[dict], dict]:
    import asyncio
    from insider_screener import _aggregate, _fetch_all, _rank, get_form4_accessions

    t0 = time.perf_counter()
    accs = get_form4_accessions(START, END)
    t_index = time.perf_counter() - t0

    t1 = time.perf_counter()
    rows = asyncio.run(_fetch_all(accs))
    t_fetch = time.perf_counter() - t1

    t2 = time.perf_counter()
    df = _rank(_aggregate(rows), top=10_000, min_value=0)
    t_agg = time.perf_counter() - t2

    out = df.to_dict("records") if not df.empty else []
    return out, {"index_s": t_index, "fetch_s": t_fetch, "agg_s": t_agg, "rows": len(rows)}


# ─── B: pandas from DERA TSV ─────────────────────────────────────────────────
def approach_b_pandas(zip_path: Path) -> tuple[list[dict], dict]:
    import pandas as pd

    t0 = time.perf_counter()
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("SUBMISSION.tsv") as f:
            sub = pd.read_csv(f, sep="\t", dtype=str, low_memory=False)
        with zf.open("NONDERIV_TRANS.tsv") as f:
            tx = pd.read_csv(f, sep="\t", dtype=str, low_memory=False)
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    tx["TRANS_SHARES"] = pd.to_numeric(tx["TRANS_SHARES"], errors="coerce")
    tx["TRANS_PRICEPERSHARE"] = pd.to_numeric(tx["TRANS_PRICEPERSHARE"], errors="coerce")
    tx["value"] = tx["TRANS_SHARES"] * tx["TRANS_PRICEPERSHARE"]
    buys = tx[(tx["TRANS_CODE"] == "P") & (tx["TRANS_DATE"] >= START) & (tx["TRANS_DATE"] <= END)]
    merged = buys.merge(
        sub[["ACCESSION_NUMBER", "ISSUERTRADINGSYMBOL", "ISSUERNAME"]],
        on="ACCESSION_NUMBER",
        how="left",
    )
    agg = (
        merged.groupby("ISSUERTRADINGSYMBOL", dropna=True)
        .agg(total_value=("value", "sum"), txns=("value", "size"))
        .reset_index()
        .sort_values("total_value", ascending=False)
    )
    t_query = time.perf_counter() - t1
    return agg.to_dict("records"), {
        "load_s": t_load,
        "query_s": t_query,
        "sub_rows": len(sub),
        "tx_rows": len(tx),
        "purchase_rows": int(len(buys)),
    }


# ─── C: polars from DERA TSV ─────────────────────────────────────────────────
def approach_c_polars(zip_path: Path) -> tuple[list[dict], dict]:
    import polars as pl

    t0 = time.perf_counter()
    with zipfile.ZipFile(zip_path) as zf:
        sub = pl.read_csv(
            io.BytesIO(zf.read("SUBMISSION.tsv")), separator="\t", infer_schema_length=0
        )
        tx = pl.read_csv(
            io.BytesIO(zf.read("NONDERIV_TRANS.tsv")), separator="\t", infer_schema_length=0
        )
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    tx = tx.with_columns(
        pl.col("TRANS_SHARES").cast(pl.Float64, strict=False),
        pl.col("TRANS_PRICEPERSHARE").cast(pl.Float64, strict=False),
    ).with_columns((pl.col("TRANS_SHARES") * pl.col("TRANS_PRICEPERSHARE")).alias("value"))
    buys = tx.filter(
        (pl.col("TRANS_CODE") == "P")
        & (pl.col("TRANS_DATE") >= START)
        & (pl.col("TRANS_DATE") <= END)
    )
    agg = (
        buys.join(
            sub.select("ACCESSION_NUMBER", "ISSUERTRADINGSYMBOL", "ISSUERNAME"),
            on="ACCESSION_NUMBER",
            how="left",
        )
        .group_by("ISSUERTRADINGSYMBOL")
        .agg(total_value=pl.col("value").sum(), txns=pl.col("value").count())
        .sort("total_value", descending=True)
    )
    t_query = time.perf_counter() - t1
    return agg.to_dicts(), {
        "load_s": t_load,
        "query_s": t_query,
        "sub_rows": sub.height,
        "tx_rows": tx.height,
        "purchase_rows": buys.height,
    }


# ─── D: DuckDB direct query over extracted TSVs ──────────────────────────────
def approach_d_duckdb(zip_path: Path) -> tuple[list[dict], dict]:
    import duckdb

    # DuckDB can't read from zip directly; extract once to a sibling dir.
    extract_dir = zip_path.with_suffix("")
    extract_dir.mkdir(exist_ok=True)
    t_extract0 = time.perf_counter()
    with zipfile.ZipFile(zip_path) as zf:
        for name in ("SUBMISSION.tsv", "NONDERIV_TRANS.tsv"):
            out = extract_dir / name
            if not out.exists():
                out.write_bytes(zf.read(name))
    t_extract = time.perf_counter() - t_extract0

    con = duckdb.connect()
    t0 = time.perf_counter()
    con.execute(
        f"""
        CREATE OR REPLACE VIEW sub AS SELECT * FROM read_csv_auto('{extract_dir}/SUBMISSION.tsv', delim='\t', header=true);
        CREATE OR REPLACE VIEW tx  AS SELECT * FROM read_csv_auto('{extract_dir}/NONDERIV_TRANS.tsv', delim='\t', header=true);
        """
    )
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    rows = con.execute(
        """
        SELECT sub.ISSUERTRADINGSYMBOL AS ticker,
               SUM(TRY_CAST(tx.TRANS_SHARES AS DOUBLE) * TRY_CAST(tx.TRANS_PRICEPERSHARE AS DOUBLE)) AS total_value,
               COUNT(*) AS txns
        FROM tx JOIN sub USING (ACCESSION_NUMBER)
        WHERE tx.TRANS_CODE = 'P'
          AND tx.TRANS_DATE BETWEEN ? AND ?
        GROUP BY 1
        ORDER BY total_value DESC
        """,
        [START, END],
    ).fetchall()
    t_query = time.perf_counter() - t1

    cnt = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
    sub_cnt = con.execute("SELECT COUNT(*) FROM sub").fetchone()[0]
    buy_cnt = con.execute(
        "SELECT COUNT(*) FROM tx WHERE TRANS_CODE='P' AND TRANS_DATE BETWEEN ? AND ?",
        [START, END],
    ).fetchone()[0]
    out = [{"ticker": r[0], "total_value": r[1], "txns": r[2]} for r in rows]
    return out, {
        "extract_s": t_extract,
        "load_s": t_load,
        "query_s": t_query,
        "sub_rows": sub_cnt,
        "tx_rows": cnt,
        "purchase_rows": buy_cnt,
    }


# ─── runner ──────────────────────────────────────────────────────────────────
@dataclass
class Result:
    name: str
    total_s: float
    stats: dict
    top3: list[tuple[str, float]]


def _top3(rows: list[dict]) -> list[tuple[str, float]]:
    out = []
    for r in rows[:3]:
        t = r.get("ticker") or r.get("ISSUERTRADINGSYMBOL") or r.get("issuer") or "?"
        v = r.get("total_value") or r.get("total_$") or 0
        if isinstance(v, str):
            v = float(v.replace("$", "").replace(",", "") or 0)
        out.append((str(t), float(v or 0)))
    return out


def main() -> None:
    print(f"=== window {START} → {END}, DERA quarter {YEAR}Q{QUARTER} ===\n")

    print("[download] DERA Insider Transactions quarter…")
    try:
        zip_path = download_quarter(YEAR, QUARTER)
    except Exception as e:
        print(f"  FAILED: {e}")
        print("  (SEC likely rate-limiting; re-run later.)")
        return
    print(f"  zip: {zip_path}")
    print(f"  members: {list_zip_members(zip_path)[:8]} …\n")

    results: list[Result] = []

    for name, fn, args in [
        ("A. Live scrape (EFTS+httpx+lxml)", approach_a_live, ()),
        ("B. pandas  (DERA TSV)", approach_b_pandas, (zip_path,)),
        ("C. polars  (DERA TSV)", approach_c_polars, (zip_path,)),
        ("D. DuckDB  (DERA TSV)", approach_d_duckdb, (zip_path,)),
    ]:
        print(f"[run] {name}")
        t0 = time.perf_counter()
        try:
            rows, stats = fn(*args)
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}\n")
            continue
        elapsed = time.perf_counter() - t0
        results.append(Result(name, elapsed, stats, _top3(rows)))
        print(f"  total {elapsed:.2f}s | {stats}")
        print(f"  top3: {_top3(rows)}\n")

    if not results:
        return
    print("\n=== summary ===")
    print(f"{'approach':42s}  {'total s':>10s}  top ticker")
    for r in results:
        top = r.top3[0] if r.top3 else ("-", 0.0)
        print(f"{r.name:42s}  {r.total_s:>10.2f}  {top[0]} ${top[1]:,.0f}")


if __name__ == "__main__":
    main()

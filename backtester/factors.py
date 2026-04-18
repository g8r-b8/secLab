"""Fama-French factor attribution for strategy returns.

Downloads Ken French's daily factor CSVs (FF3 + Momentum), caches locally,
and runs OLS of strategy excess returns on the factors. Reports annualised
alpha, factor betas with t-stats, and R². OLS done manually with numpy so we
don't drag in statsmodels.

Factor data source:
  https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html

Interpretation:
  alpha_ann > 0 and t(alpha) > 2  ⇒  strategy earns real residual return
                                     after stripping out market/size/value/
                                     momentum exposure.
"""
from __future__ import annotations

import io
import logging
import math
import zipfile
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

log = logging.getLogger("seclab")

FF3_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"

_HEADERS = {
    # Ken French's server rejects the default python-httpx UA.
    "User-Agent": "Mozilla/5.0 (secLab research)"
}

_CACHE_DIR = Path(__file__).parent.parent / ".cache" / "factors"
_DF_CACHE: pd.DataFrame | None = None
_DF_CACHE_FF5: pd.DataFrame | None = None


def _ensure_cache() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _download(url: str, fname: str) -> bytes:
    path = _ensure_cache() / fname
    if path.exists():
        return path.read_bytes()
    log.info(f"[factors] downloading {url}")
    r = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=60)
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.content


def _parse_ff_csv(raw: bytes) -> pd.DataFrame:
    """Ken French CSVs have a preamble and sometimes multiple sections.

    Strategy: unzip, read lines, find the first line whose first token is a
    length-8 digit string (YYYYMMDD). Everything from there until the next
    blank or non-numeric line is the daily section. Columns come from the
    last header line before the data.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        text = z.read(name).decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Find the start of the first daily section
    start = None
    header: list[str] | None = None
    for i, line in enumerate(lines):
        tokens = [t.strip() for t in line.split(",")]
        if tokens and tokens[0].isdigit() and len(tokens[0]) == 8:
            start = i
            break
        # Remember header candidates (last non-data line before start)
        if tokens and any(tokens[1:]):
            header = tokens

    if start is None or header is None:
        raise ValueError("couldn't locate daily section in Ken French CSV")

    # Collect numeric data lines until a blank / non-numeric row
    data_rows: list[list[str]] = []
    for line in lines[start:]:
        tokens = [t.strip() for t in line.split(",")]
        if not tokens or not tokens[0] or not tokens[0].isdigit() or len(tokens[0]) != 8:
            break
        data_rows.append(tokens)

    df = pd.DataFrame(data_rows, columns=header)
    df[header[0]] = pd.to_datetime(df[header[0]], format="%Y%m%d")
    df = df.rename(columns={header[0]: "date"}).set_index("date")
    # Values are in percent; convert to decimal
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0
    return df.dropna()


def load_factors(force_reload: bool = False) -> pd.DataFrame:
    """Return daily FF3 + momentum factors as a DataFrame indexed by date.

    Columns: Mkt-RF, SMB, HML, RF, MOM (all decimals, not percent).
    """
    global _DF_CACHE
    if _DF_CACHE is not None and not force_reload:
        return _DF_CACHE

    ff3_raw = _download(FF3_URL, "FF3_daily.zip")
    ff3 = _parse_ff_csv(ff3_raw)
    # FF3 columns expected: Mkt-RF, SMB, HML, RF
    ff3.columns = [c.strip() for c in ff3.columns]

    mom_raw = _download(MOM_URL, "Momentum_daily.zip")
    mom = _parse_ff_csv(mom_raw)
    mom.columns = [c.strip() for c in mom.columns]
    # Momentum CSV column is "Mom" or "UMD"; normalise.
    rename = {c: "MOM" for c in mom.columns if c.upper() in ("MOM", "UMD")}
    mom = mom.rename(columns=rename)[["MOM"]]

    out = ff3.join(mom, how="left")
    _DF_CACHE = out
    return out


def load_factors_ff5(force_reload: bool = False) -> pd.DataFrame:
    """FF5 = Mkt-RF, SMB, HML, RMW (profitability), CMA (investment), RF, + MOM.

    Same daily series as FF3 but with the Fama-French 5-factor set — adds
    RMW (Robust-Minus-Weak profitability) and CMA (Conservative-Minus-
    Aggressive investment) to capture quality-factor tilts that the older
    3-factor model missed.
    """
    global _DF_CACHE_FF5
    if _DF_CACHE_FF5 is not None and not force_reload:
        return _DF_CACHE_FF5

    ff5_raw = _download(FF5_URL, "FF5_daily.zip")
    ff5 = _parse_ff_csv(ff5_raw)
    ff5.columns = [c.strip() for c in ff5.columns]

    mom_raw = _download(MOM_URL, "Momentum_daily.zip")
    mom = _parse_ff_csv(mom_raw)
    mom.columns = [c.strip() for c in mom.columns]
    rename = {c: "MOM" for c in mom.columns if c.upper() in ("MOM", "UMD")}
    mom = mom.rename(columns=rename)[["MOM"]]

    out = ff5.join(mom, how="left")
    _DF_CACHE_FF5 = out
    return out


def _ols(y: np.ndarray, X: np.ndarray) -> dict[str, Any]:
    """Plain OLS with HAC-less classical SEs. X must include constant column."""
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 0 else 0.0
    dof = max(1, n - k)
    sigma2 = rss / dof
    xtx_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.clip(np.diag(sigma2 * xtx_inv), 0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, 0.0)
    return {
        "beta": beta.tolist(),
        "se": se.tolist(),
        "t": t.tolist(),
        "r2": r2,
        "n": n,
        "resid_std": math.sqrt(sigma2),
    }


def attribute_returns(
    dates: list[str],
    strategy_returns: list[float],
) -> dict[str, Any] | None:
    """Regress strategy daily returns on FF3 + momentum factors.

    dates: ISO dates aligned with strategy_returns (each return is the
           *next* day's return off the equity curve — i.e. returns[i] is
           the return on dates[i]).
    Returns a dict with alpha, betas, t-stats, R², n_obs, factor coverage.
    None if we can't fetch factor data or if overlap < 30 days.
    """
    if len(dates) != len(strategy_returns) or len(dates) < 30:
        return None
    try:
        factors = load_factors()
    except Exception as e:
        log.warning(f"[factors] load failed: {e}")
        return None

    idx = pd.to_datetime(dates)
    rets = pd.Series(strategy_returns, index=idx, name="strategy").astype(float)
    # Join on date, drop rows where any factor is missing
    merged = rets.to_frame().join(factors, how="inner").dropna()
    if len(merged) < 30:
        return None

    # y = excess strategy return (subtract risk-free)
    y = (merged["strategy"] - merged["RF"]).to_numpy()
    factor_cols = ["Mkt-RF", "SMB", "HML", "MOM"]
    available = [c for c in factor_cols if c in merged.columns and merged[c].notna().all()]
    X_raw = merged[available].to_numpy()
    X = np.column_stack([np.ones(len(y)), X_raw])
    res = _ols(y, X)

    labels = ["alpha"] + available
    coef = dict(zip(labels, res["beta"]))
    se = dict(zip(labels, res["se"]))
    t = dict(zip(labels, res["t"]))

    alpha_daily = float(coef["alpha"])
    return {
        "alpha_daily": alpha_daily,
        "alpha_annualized": (1 + alpha_daily) ** 252 - 1,
        "alpha_t": float(t["alpha"]),
        "alpha_se_daily": float(se["alpha"]),
        "alpha_significant": abs(float(t["alpha"])) > 2.0,
        "factors": {
            name: {
                "beta": float(coef[name]),
                "se": float(se[name]),
                "t": float(t[name]),
                "significant": abs(float(t[name])) > 2.0,
            }
            for name in available
        },
        "r_squared": float(res["r2"]),
        "n_obs": int(res["n"]),
        "residual_std_daily": float(res["resid_std"]),
        "factor_date_range": {
            "start": merged.index.min().date().isoformat(),
            "end": merged.index.max().date().isoformat(),
        },
    }


def attribute_returns_ff5(
    dates: list[str],
    strategy_returns: list[float],
) -> dict[str, Any] | None:
    """FF5 + MOM regression (Mkt-RF, SMB, HML, RMW, CMA, MOM).

    Same contract as attribute_returns but uses the 5-factor model plus
    momentum. RMW = profitability (quality), CMA = investment-style factor.
    A residual α that was significant under FF3+MOM may shrink under FF5
    if the strategy was really capturing quality or low-investment effects.
    """
    if len(dates) != len(strategy_returns) or len(dates) < 30:
        return None
    try:
        factors = load_factors_ff5()
    except Exception as e:
        log.warning(f"[factors FF5] load failed: {e}")
        return None

    idx = pd.to_datetime(dates)
    rets = pd.Series(strategy_returns, index=idx, name="strategy").astype(float)
    merged = rets.to_frame().join(factors, how="inner").dropna()
    if len(merged) < 30:
        return None

    y = (merged["strategy"] - merged["RF"]).to_numpy()
    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]
    available = [c for c in factor_cols if c in merged.columns and merged[c].notna().all()]
    X_raw = merged[available].to_numpy()
    X = np.column_stack([np.ones(len(y)), X_raw])
    res = _ols(y, X)

    labels = ["alpha"] + available
    coef = dict(zip(labels, res["beta"]))
    se = dict(zip(labels, res["se"]))
    t = dict(zip(labels, res["t"]))

    alpha_daily = float(coef["alpha"])
    return {
        "alpha_daily": alpha_daily,
        "alpha_annualized": (1 + alpha_daily) ** 252 - 1,
        "alpha_t": float(t["alpha"]),
        "alpha_se_daily": float(se["alpha"]),
        "alpha_significant": abs(float(t["alpha"])) > 2.0,
        "factors": {
            name: {
                "beta": float(coef[name]),
                "se": float(se[name]),
                "t": float(t[name]),
                "significant": abs(float(t[name])) > 2.0,
            }
            for name in available
        },
        "r_squared": float(res["r2"]),
        "n_obs": int(res["n"]),
        "residual_std_daily": float(res["resid_std"]),
        "factor_date_range": {
            "start": merged.index.min().date().isoformat(),
            "end": merged.index.max().date().isoformat(),
        },
    }

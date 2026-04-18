"""GARCH(1,1) conditional-volatility model for strategy returns.

Standard financial-econometrics vol model: σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}.
Given a series of daily strategy returns, we fit (ω, α, β) via MLE and return
the time-varying conditional vol σ_t. Persistence = α + β; values near 1
mean vol shocks decay slowly (clustering).

Interpretation for a trader:
  - When conditional σ_t spikes, risk per position should shrink.
  - A `vol_target` sizing rule keyed off GARCH σ_t (rather than a 60-day
    rolling estimate) adapts faster to regime changes.
  - Persistence > 0.95 means the strategy inherits vol clustering from the
    underlying market; < 0.9 means vol reverts quickly to mean.
"""
from __future__ import annotations

import logging
import math
import warnings
from typing import Any

log = logging.getLogger("seclab")


def fit_garch_1_1(
    returns: list[float],
    dates: list[str] | None = None,
    forecast_horizon: int = 20,
) -> dict[str, Any] | None:
    """Fit GARCH(1,1) on a daily return series.

    Returns a dict with:
      - `coefficients`: (mu, omega, alpha[1], beta[1])
      - `persistence`: alpha + beta
      - `half_life_days`: log(0.5) / log(persistence), or None if persistence>=1
      - `unconditional_vol_ann`: long-run σ annualized (sqrt(252))
      - `conditional_vol`: list[{date, sigma_ann}] — time-varying σ
      - `forecast_vol_ann`: forecast at `forecast_horizon` days out
      - `n_obs`, `loglik`, `aic`, `bic`

    Returns None if fit fails or inputs are too short.
    """
    if len(returns) < 60:
        return None
    try:
        from arch import arch_model  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:
        log.warning(f"[garch] arch / numpy import failed: {e}")
        return None

    r = np.asarray(returns, dtype=float)
    if not np.isfinite(r).all():
        # arch doesn't tolerate NaN/Inf
        finite = np.isfinite(r)
        r = r[finite]
        dates = [d for d, f in zip(dates or [], finite)] if dates else None
        if len(r) < 60:
            return None

    # arch expects returns in %; scale for numerical stability
    r_pct = r * 100.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = arch_model(
                r_pct, mean="Constant", vol="GARCH", p=1, q=1, dist="normal", rescale=False
            )
            res = model.fit(disp="off", show_warning=False)
        except Exception as e:
            log.warning(f"[garch] fit failed: {e}")
            return None

    params = res.params
    mu = float(params.get("mu", 0.0)) / 100.0  # back from %
    omega = float(params.get("omega", 0.0))
    alpha = float(params.get("alpha[1]", 0.0))
    beta = float(params.get("beta[1]", 0.0))
    persistence = alpha + beta

    # Reject degenerate / non-stationary fits outright instead of returning
    # half-NaN numbers that blow up the UI.
    if (
        not math.isfinite(omega)
        or not math.isfinite(alpha)
        or not math.isfinite(beta)
        or not math.isfinite(persistence)
    ):
        log.warning(f"[garch] degenerate fit: omega={omega} alpha={alpha} beta={beta}")
        return None

    # Conditional vol series (arch returns in % daily). Annualize.
    cond_daily = np.asarray(res.conditional_volatility) / 100.0
    cond_ann = cond_daily * math.sqrt(252)

    # Unconditional (long-run) vol
    try:
        long_run_var_pct = omega / max(1 - persistence, 1e-8)  # in (%)^2
        long_run_vol_daily = math.sqrt(max(long_run_var_pct, 0.0)) / 100.0
        uncond_ann = long_run_vol_daily * math.sqrt(252)
    except Exception:
        uncond_ann = float(np.std(r) * math.sqrt(252))

    # Half-life of vol shock: persistence^k = 0.5 → k = log(0.5) / log(persistence)
    half_life = None
    if 0 < persistence < 1:
        try:
            half_life = math.log(0.5) / math.log(persistence)
        except Exception:
            half_life = None

    # n-day-ahead vol forecast (arch's forecast)
    forecast_ann = None
    try:
        fc = res.forecast(horizon=max(1, int(forecast_horizon)), reindex=False)
        last_var_pct = float(fc.variance.iloc[-1].iloc[-1])  # last horizon, (%)^2
        forecast_ann = math.sqrt(max(last_var_pct, 0.0)) / 100.0 * math.sqrt(252)
    except Exception:
        pass

    # Align dates if provided
    cond_series: list[dict[str, Any]] = []
    if dates and len(dates) == len(returns):
        # cond_ann aligns with the same index as returns (after arch's internal
        # handling). In practice arch fits on the full series; if lengths
        # diverge (rare), fall back to trailing alignment.
        offset = len(returns) - len(cond_ann)
        for i, sigma in enumerate(cond_ann):
            idx = i + offset
            if 0 <= idx < len(dates):
                cond_series.append({"date": dates[idx], "sigma_ann": float(sigma)})
    else:
        for i, sigma in enumerate(cond_ann):
            cond_series.append({"date": f"t+{i}", "sigma_ann": float(sigma)})

    return {
        "coefficients": {
            "mu": mu,
            "omega": omega,
            "alpha": alpha,
            "beta": beta,
        },
        "persistence": persistence,
        "half_life_days": half_life,
        "unconditional_vol_ann": uncond_ann,
        "current_conditional_vol_ann": float(cond_ann[-1]) if len(cond_ann) else None,
        "forecast_vol_ann": forecast_ann,
        "forecast_horizon_days": int(forecast_horizon),
        "conditional_vol": cond_series,
        "n_obs": int(len(r)),
        "loglik": float(res.loglikelihood),
        "aic": float(res.aic),
        "bic": float(res.bic),
    }

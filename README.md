# secLab

Self-hosted SEC-filings analytics platform. Built on DERA bulk data + EDGAR
live scrapes, stored in DuckDB, served by FastAPI, rendered by SvelteKit.

Three pillars, one pipeline:

1. **FactSet / Compustat clone** — XBRL fundamentals for the full US-listed
   universe, cross-sectional screener with 20+ ratios plus Piotroski F-score
   and Magic Formula composites.
2. **Insider-trading dashboards** — Form 4 / 144 / 13D / 13G / Form 3 / 8-K /
   late filings / proxy contests / secondaries / tender offers / delistings /
   foreign issuers / 13F. Every filing type with a testable edge has its own
   tab and price-impact chart.
3. **Backtesting → paper-trading** — event-driven engine with six position-
   sizing modes, realistic execution costs, parameter sweep, walk-forward
   validation, random-signal permutation test, and a daily-idempotent paper-
   book that forward-tests the champion config on fresh data.

Quant rigor layer across all three: bootstrap Sharpe CI, Fama-French factor
attribution, GARCH(1,1) conditional volatility, Cornish-Fisher heavy-tails
VaR, correlation-aware sizing, survivorship-bias surfacing, turnover + TCA.

---

## Setup

Prereqs:
- Python 3.12+
- Node 20+
- `git`, `make`-ish shell (bash/zsh)
- ~30 GB free disk for the full DERA backfill + price cache (first-run)

```bash
# 1. Clone + Python env
git clone git@github.com:g8r-b8/secLab.git
cd secLab
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Copy env template and fill in optional keys
cp .env.example .env
# Edit .env — both keys are OPTIONAL but unlock real features:
#   TIINGO_API_KEY    — backfill delisted tickers (fixes ~27% survivorship gap)
#   FINNHUB_API_KEY   — current-quarter insider filings before SEC publishes DERA

# 3. Frontend
cd web && npm install && cd ..
```

## First run

Start the API and the frontend in two terminals:

```bash
# Terminal 1 — FastAPI on :8000
.venv/bin/uvicorn api:app --reload --port 8000

# Terminal 2 — SvelteKit dev server on :5173 (proxies /api to :8000)
cd web && npm run dev
```

Open http://localhost:5173. The first page load ingests quarterly DERA bulk
data (insider transactions + XBRL fundamentals) into a local DuckDB file at
`./seclab.duckdb`. **Expect 15–30 minutes on the first load** — subsequent
queries are sub-second against the local DB.

For faster cold start, pre-populate one window before booting the UI:

```bash
.venv/bin/python -c "
from data_store import ensure_insider_quarters, ensure_fundamentals_quarters
ensure_insider_quarters('2024-01-01', '2026-12-31')
ensure_fundamentals_quarters('2024-01-01', '2026-12-31')
"
```

## Daily refresh

A single command pulls fresh SEC data and advances every active paper
portfolio. Idempotent — safe to call multiple times a day.

```bash
.venv/bin/python scripts/daily_refresh.py

# Flags:
#   --days 60         # wider EFTS scrape window
#   --skip-scrape     # just DERA + paper advance
#   --skip-dera       # just EFTS + paper advance
#   --skip-paper      # data only, no paper bookkeeping
#   --as-of 2026-03-15  # pin the "today" for reproducible runs
```

Wire to cron / launchd for hands-off operation. Example:

```cron
# Every weekday at 7pm local, after US close
0 19 * * 1-5  cd /path/to/secLab && .venv/bin/python scripts/daily_refresh.py
```

---

## Directory layout

```
secLab/
├── api.py                     FastAPI endpoints (backtest, screener, paper, all dashboards)
├── data_store.py              DuckDB schema + all ensure_*_window ingest helpers
├── scripts/
│   └── daily_refresh.py       Scheduled data pull + paper advance
├── backtester/
│   ├── core.py                Event-driven engine, 6 sizing modes, all exit rules
│   ├── signals.py             Signal generators (cluster_buys, csuite_buys, ...,
│   │                          piotroski_long, auditor_changes, stacked_*)
│   ├── metrics.py             Sharpe, Sortino, VaR, CF-VaR, drawdown, monthly heatmap,
│   │                          info ratio, tracking error
│   ├── factors.py             Fama-French 3+MOM factor attribution
│   ├── garch.py               GARCH(1,1) conditional volatility
│   ├── baseline.py            Random-signal permutation test
│   ├── sweep.py               Parameter grid sweep (ProcessPool-parallel)
│   ├── walkforward.py         Walk-forward train/test validation
│   └── forward.py             Paper-trade harness (idempotent advance)
├── providers/
│   ├── prices.py              yfinance primary, Tiingo fallback, disk cache
│   ├── finnhub.py             Insider fallback for current quarter
│   ├── form_8k.py             8-K EFTS scraper with item codes
│   ├── form_13f.py            13F holdings parser
│   ├── thirteen_d.py          Schedule 13D scraper
│   ├── form144.py             Form 144 planned sales
│   ├── tender_offers.py       SC TO-T / 14D9
│   ├── proxy_contests.py      DFAN / DEFC / PRRN14A
│   ├── secondaries.py         S-3 / 424B
│   ├── late_filings.py        NT 10-K / NT 10-Q
│   └── simple_filings.py      Generic EFTS helper
├── web/
│   └── src/routes/            SvelteKit pages — one per dashboard
│       ├── insider-buying/    Form 4 cluster + C-suite buys
│       ├── activism/          Schedule 13D
│       ├── schedule-13g/      Passive 5%+
│       ├── form-3/            New-insider onboarding
│       ├── form-d/            Private placements
│       ├── events/            8-K material events
│       ├── late-filings/      NT 10-K/Q
│       ├── proxy-contests/    Activist contests
│       ├── secondaries/       Dilution feed
│       ├── tender-offers/     M&A targets
│       ├── delistings/        Form 15 / 25
│       ├── foreign-issuers/   20-F / 6-K
│       ├── superinvestors/    13F fund holdings
│       ├── fundamentals/[ticker]/   Per-ticker IS/BS/CF + ratios + dossier
│       ├── screener/          Cross-sectional ratios + Piotroski + Magic Formula
│       ├── backtest/          Full backtest UI (equity, drawdown, VaR,
│       │                      GARCH, FF attribution, baseline test, etc.)
│       ├── backtest/sweep/    Parameter-grid heatmap
│       ├── backtest/walkforward/  In/out-of-sample validation
│       └── paper/             Forward-test portfolios
├── .env.example               Template for optional API keys
├── .gitignore                 Excludes .venv, .cache, seclab.duckdb, node_modules
├── requirements.txt           Python deps
└── TODO.md                    Roadmap + completed milestones
```

## Champion config

Default on `/backtest` is seeded to the 27-combo sweep champion from
2022-01-01 → 2024-12-31 on the `cluster_buys` preset:

| | |
|---|---|
| Sizing | `strength_weighted` · base 2% · cap 3× |
| Hold | 5 days |
| Stop-loss | 3% |
| Profit target | 10% |
| Slippage | 10 bps each side |
| Commission | $1 / fill |
| **Sharpe** | **3.18** |
| **Return** | **+302%** |
| **Alpha vs SPY** | **+279%** |
| **FF annualized α** | **+55.9%** (t=5.14, significant) |

⚠️ The current default reflects a 27.1% survivorship gap — about 2,851
signals on delisted tickers are excluded by yfinance. Set `TIINGO_API_KEY` in
`.env` to close the gap; the fallback is already wired into `_fetch_prices`.

## Honest-numbers checklist

Every non-trivial claim on `/backtest` is backed by one of:

- **Bootstrap Sharpe CI** (1000 resamples) — is the Sharpe statistically > 0?
- **Random-signal baseline** — permutation test that shuffles (ticker, date)
  pairings. If real Sharpe ≤ random p95, it's noise.
- **Fama-French factor attribution** — how much of the return is factor
  beta vs genuine residual α? With t-stat.
- **Survivorship panel** — which tickers / how many signals got dropped for
  missing price data.
- **GARCH conditional vol** — is the Sharpe achieved at low or high σ?
- **Cornish-Fisher VaR** — parametric VaR adjusted for skew + kurtosis.
- **Turnover + TCA** — what fraction of gross return did costs eat?
- **Walk-forward validation** — do params picked in-sample hold
  out-of-sample?

---

## Data pipeline

```
SEC DERA bulk ZIPs (quarterly)  ──┐
  insider-transactions               │
  financial-statement-data-sets      │
                                     ▼
EDGAR full-text-search (daily)   ───► DuckDB (seclab.duckdb)  ──► FastAPI
  Form 4 / 144 / 13D / 13G / 3        (local, ~8 GB)              (:8000)
  8-K / NT 10-K / DEFC / PRRN14A                                    │
  S-3 / 424B / SC TO-T / 14D9                                       ▼
  Form 15/25 / 20-F / 6-K                                       SvelteKit
                                                                    (:5173)
Ken French factor CSVs (monthly)  ───► .cache/factors/
yfinance + Tiingo (on-demand)     ───► .cache/prices/ (per-ticker parquet)
```

The daily refresh script hits all three rows. First DERA ingest per quarter
takes a couple of minutes; afterwards it's skipped via `_ingested_quarters`
table. Prices are disk-cached with a negative cache for delisted names (24h
TTL) so Yahoo rate-limits don't wedge the backtester.

---

## License

MIT (pending — open-source-friendly). Non-commercial research use.

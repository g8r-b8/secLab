# secLab TODO

**Vision:** a free, self-hosted stack with:
1. **FactSet / Compustat clone** — SEC-derived fundamentals in DuckDB, cross-sectional screens
2. **Insider trading dashboard** — Form 4 purchases, cluster buys, C-suite signal (in progress)
3. **Backtesting + forward testing engine** — with its own dashboard

Plan file: `~/.claude/plans/abstract-tinkering-dragonfly.md`

---

## Pillar 2 — Insider trading (current sprint)
- [x] Scaffold FastAPI + SvelteKit frontend (insider-buying page live at http://localhost:5173/insider-buying)
- [x] Throttle SEC scraper (8 req/s semaphore, 429-aware retry)
- [x] Confirm DERA Insider Transactions URL + profile pandas/polars/duckdb ingestion
- [x] **Fix DERA query** — `TRANS_DATE` cast to DATE on ingest (DD-MON-YYYY)
- [x] `data_store.py` — DuckDB schema, `ensure_insider_quarters`, `get_insider_purchases`
- [x] Rewire `api.py` to query DuckDB; warnings + provider status in response
- [x] `providers/finnhub.py` — fallback for current (un-released) quarter filings
- [x] `.gitignore`, `.env.example`
- [x] End-to-end smoke test via Vite proxy — 1,167 purchases / $2.09B over 2026-03-01→2026-03-15, query <50 ms
- [ ] Delete `dashboard.py` (Streamlit → replaced by SvelteKit) — awaiting explicit OK (repo has no git commits, so no safety net)
- [ ] User: add `FINNHUB_API_KEY` to `.env` to unlock current-quarter live fallback

## Per-ticker research
- [x] **Ticker dossier tab** — `/fundamentals/:ticker` now has a **SEC Activity** tab that aggregates insider buys, planned sells (144), 13D/13G, 8-K items, NT filings, proxy contests, secondaries, tender offers, Form 3 new insiders, and superinvestor holders from fund_holdings — all via one `/tickers/:ticker/dossier` endpoint.

## Pillar 1 — FactSet / Compustat clone
- [x] Ingest SEC **Financial Statement Data Sets** (XBRL, `~65 MB/quarter`) into DuckDB (tables `fsds_sub`, `fsds_num`, `fsds_tag`, `fsds_pre`) — Q4 2025 loaded in 10s
- [x] Ticker↔CIK mapping via `company_tickers.json` (10,391 rows)
- [x] EOD prices layer — yfinance (raw closes, split-safe for Form 4 event studies)
- [x] Svelte `/fundamentals/:ticker` page — IS / BS / CF tables, AAPL verified (Rev $416B / NI $112B / EPS $7.46)
- [x] Backfill last 5 years of FSDS quarters — 2021Q1 → 2026Q1 in DuckDB (~500k filings, ~10min one-time ingest)
- [x] Standardized metrics view — tag alias resolver in `data_store.get_key_ratios` picks first working alias per metric (revenue, NI, EBITDA, FCF, etc.)
- [x] Key ratios on the fundamentals page — P/E, P/B, P/S, EV/EBITDA, FCF yield, margins, ROE/ROA, D/E, net debt, YoY growth. AAPL verified: P/E 36.4, ROE 151.9%, FCF yield 2.5%, revenue +6.4%
- [x] Ratios snapshot table + screener — `compute_ratios_snapshot` pulls all 10-K num rows in ONE SQL + batches yfinance prices; 6,656 tickers / ~4 min. `/screener` page with filter-by-ratio sliders, sortable columns, links to `/fundamentals/:ticker`. NVDA/GOOGL/AAPL top the ROE≥20 + NM≥20 filter.
- [x] **Piotroski F-score (reduced 5-of-9) + Magic Formula rank** — computed inline on ratios_snapshot via SQL, no schema change. Piotroski tests: NI>0, ROA>0, revenue growth>0, NI growth>0, D/E<1. Magic Formula approx: sum of ranks on earnings_yield + ROA. Screener: new Q and MF sortable columns, `quality_score_min` + `magic_rank_max` filters, "Exclude ETFs" toggle (defaults on — filters out TRUST / ETF / FUND / PROSHARES / ISHARES / SPDR / ABRDN). Top mid-cap value+quality picks: BYD, VAL, CF Industries, Dropbox, ADBE.
- [x] **`piotroski_long` backtest preset** — quarterly rebalance into top-N by magic_rank among quality_score ≥ 4, mkt cap ≥ $1B, ETFs excluded. Smoke test 2024→today (60d hold, 5% position): Sharpe 1.06, return +31.5%, **alpha vs SPY −10.0%** (underperformed), FF α +3.3% with t=0.51 (not significant). Quality/value was out of favor — insider signals crush factor screens this window. Note: uses current snapshot for historical dates; needs point-in-time data (Bundle C) for honest historical backtests.
- [ ] Screener factor-stacking UI (drag-and-drop to combine factors with weights)

## Pillar 3 — Backtesting + forward testing
- [x] Event-driven backtester core (daily MTM, T+1 close entry, N-day hold, fixed $ sizing) at `backtester/core.py`
- [x] Signal generators — cluster_buys, csuite_buys, form144_cluster_sells — `backtester/signals.py`
- [x] Metrics: Sharpe, Sortino, CAGR, max DD, win rate, benchmark alpha — `backtester/metrics.py`
- [x] `/backtest/run` API + Svelte `/backtest` page with equity curve vs benchmark, trade table
- [x] Smoke-tested: cluster_buys Q1 2026 → 480 trades, CAGR 7.6%, Sharpe 0.60, alpha −0.59% vs SPY
- [x] **Parameter sweep** — `backtester/sweep.py` + `/backtest/sweep` UI runs grid of (hold × stop × target) for any preset. Shared price cache across combos, ProcessPool-parallelized across CPU cores. Top-5 leaderboards by Sharpe and alpha, heatmap-colored table. **Best config found:** cluster_buys 2022-2024 · hold=10 + stop=5% + target=15% → **Sharpe 2.74, alpha +38.2% vs SPY** (10,535 signals, 36 combos swept in 7min).
- [x] **Execution realism** — Backtest accepts `slippage_bps` (applied to entry + exit fills) and `commission_per_trade` (deducted 2× per trade). Plus `one_position_per_ticker` gating to suppress duplicate signals on the same ticker while one is already open. Wired through single backtest, sweep, and walk-forward. Defaults on `/backtest`: 10 bps + $1 per trade.
- [ ] Borrow cost for shorts, T+1/T+2 settlement
- [x] **Backtester price cache** — `_fetch_prices` now writes per-ticker parquet to `.cache/prices/{t}_{start}_{end}.parquet` + negative cache at `_negative.json` (24h TTL). Second run of the same window reads from disk (<50ms for 3 tickers) and never re-hits Yahoo. Rate-limited tickers get negative-cached so we don't hammer the same 429s across retries.

## Quant finance upgrades (audit + roadmap)

### Bundle A — "Make the numbers honest" 
- [x] **Bootstrap Sharpe CI** — resample daily returns 1000× with replacement, report [p5, median, p95]. cluster_buys 2022-H1→2023-H1: Sharpe 2.63 with 95% CI [1.39, 3.93] — CI excludes zero, signal is statistically significant.
- [x] **Rolling 60-day Sharpe** — line chart on `/backtest` showing whether alpha is stable or concentrated in one stretch.
- [x] **Drawdown / underwater chart** — separate panel, red shaded, shows equity / running_max − 1.
- [x] **Return distribution histogram** — daily returns with red (losing) / green (winning) bucket coloring, ±σ / min / max stats.
- [x] **Calmar ratio** — CAGR / |max_drawdown| surfaced on the metric strip.
- [x] **Monthly return heatmap** — year × month table on `/backtest` with green/red color scaling and annual-total column. Compounded from daily returns per calendar month.
- [x] **Max drawdown duration** — longest continuous stretch underwater (days), plus current days underwater. Displayed as a sub-label next to max drawdown.
- [x] **Information Ratio** — annualized active return / tracking error vs SPY. Displayed next to Alpha in the metric strip.

### Bundle B — "Make the sizing realistic" 
- [x] **Risk-per-trade sizing** — `position = (risk_pct × equity) / stop_pct`. Every trade risks the same dollars regardless of ticker.
- [x] **Volatility target sizing** — size to per-position annualised volatility target; σ estimated from price history.
- [x] **Signal ranking + daily bet cap** — `max_signals_per_day` keeps top-N by signal `strength`. Generators now write `strength` (cluster size × log-$ for cluster_buys, log-$ for csuite_buys, etc).
- [x] **Sector exposure caps** — `max_sector_pct` blocks new positions that would push a GICS sector over threshold. Sector lookup via yfinance.info cache.
- [x] **VaR / CVaR** — historical 95% VaR and Expected Shortfall on daily returns, both in metric row.
- [x] **Strength-weighted sizing** — `position_mode='strength_weighted'` sizes each bet as `base_pct × (signal.strength / median_strength)`, clamped to [1/cap, cap]. On cluster_buys H1-2023: Sharpe **3.12** vs 2.73 fixed_usd / 2.65 pct_portfolio — conviction weighting beats uniform sizing.
- [x] **Target-deployment sizing** — `position_mode='target_deployment'` keeps portfolio at a fixed % invested (default 80%) by dynamically sizing new positions to close the gap. Caps per-position exposure. Peak deployment hits target exactly instead of leaking above.
- [x] **Cash-clip fix** — previously pct_portfolio silently allowed implicit leverage (peak deployment 258%, cash went to −$188k). Now all compounding modes cap position at available cash; positions < $100 after clip are skipped.
- [x] **Correlation-aware sizing** — modifier flag `correlation_adjust` that shrinks new position size by `1 − avg(ρ) × shrinkage` where ρ is Pearson correlation over a rolling window (default 60d) of the new ticker's daily returns vs each open position's. Stacks on top of any sizing mode. New UI checkbox + ρ-shrinkage / lookback controls.

### Bundle C — "Fix the bias"
- [ ] Point-in-time fundamentals — use as-filed 10-K values at each trade date, not latest. Prevents lookahead in screener-backed backtests.
- [~] **Survivorship bias surfaced + Tiingo wire-up** — Backtest summary now includes `survivorship`: {n_excluded_signals, n_excluded_tickers, pct_of_total, excluded_tickers}. cluster_buys 2022-2024 champion drops **2,851 signals (27.1%) across 451 tickers** due to yfinance missing delisted data. Prominent UI warning panel shipped. `_fetch_prices` now falls back to Tiingo for any ticker yfinance returned empty — set `TIINGO_API_KEY` in `.env` to close the gap (free tier: 500/hr, 2000/day). First run after key-set will populate per-ticker parquet cache so subsequent runs don't re-hit Tiingo.
- [ ] Historical ticker_map snapshots (for a true point-in-time universe — signals on names that existed only pre-2022 still miss)
- [ ] As-of DuckDB views for time-aware queries

### Other missing quant principles
- [x] **GARCH(1,1) conditional volatility** — `backtester/garch.py` fits ω/α/β via MLE (arch pkg), returns persistence, half-life, unconditional + conditional σ series, n-day forecast. Panel on `/backtest` shows metric chips + orange σ time-series line. Champion config fit: persistence 0.958, half-life 16d, uncond σ 15.6%, current σ 11.0% (benign regime), 20-day forecast 13.6%.
- [x] **Cornish-Fisher VaR (heavy tails)** — adjusts the Normal quantile by sample skewness + excess kurtosis. Shown alongside historical + parametric VaR in the metric strip; sample skew/exK printed underneath so heavy-tails are visible.
- [ ] Fama-French 3/5 factor attribution — strip out market/size/value/momentum/quality loadings to isolate true residual alpha
- [ ] Information Ratio (vs custom benchmark) + Calmar ratio + Sortino separately
- [ ] Max drawdown duration (days underwater) not just depth
- [x] **Random-signal baseline (permutation test)** — `backtester/baseline.py` + `/backtest/random-baseline`. Keeps real signal's date distribution and per-ticker frequencies, shuffles (ticker, date) pairings K times. Reports real Sharpe vs random p5/median/p95 + p-value. UI panel on `/backtest` with "Run baseline" button + bar chart. cluster_buys H1-2023 test: real Sharpe **+2.73** vs random p95 **+0.08** (0/15 random runs came close) — alpha lives in the filing-date-to-ticker coupling, not the universe or window.
- [x] **Turnover + TCA** — annualized turnover ratio (gross notional / avg equity / years) + dollar breakdown of slippage vs commission costs and what fraction of gross profit the costs ate. Metric chips on `/backtest`.
- [x] **Beta-hedge view** — post-hoc hedged returns = strategy − β × SPY using the Fama-French Mkt-RF beta (or user-supplied). Panel on `/backtest` with hedged Sharpe, return, max DD, overlay chart. Champion result: **unhedged Sharpe 3.18 → hedged Sharpe 3.74** (β=0.46), return 302% → 263%, max DD −9.6% → **−5.12%**. Removing market beta reveals higher-quality residual α.
- [ ] Multi-factor scoring — combine multiple signals into composite Z-score
- [x] **Signal half-life study** — `backtester/halflife.py` computes cross-sectional mean + median forward-return curve (1..60 days post-entry) for any preset. Panel on `/backtest`: peak day, peak cum return, half-life, front-loading %. Cluster_buys finding: **day 1 alone = 36% of peak · day 2 captures 70% · peak at day 58 (2.47%)** — classic front-loaded insider signal, vindicates 5-day hold vs 60-day.
- [x] **Half-life comparison across presets** — `/research/halflife` page overlays decay curves for any selected presets + summary table tagging each as fast / medium / slow-build, front-loaded or not. Headline finding: **csuite_buys peaks at day 39 at +12.67% (5× cluster_buys) but only 9% of alpha on day 1 — slow-build, demands 20-40d holds**. Our csuite backtests at hold=5 capture almost none of this — massive missed alpha once we extend the hold.
- [ ] Position sizing modes: % of portfolio, Kelly, risk parity
- [ ] Dynamic exit rules: stop-loss, trailing stop, volatility-based exit
- [x] **Walk-forward validation** — `backtester/walkforward.py` + `/backtest/walkforward` UI. Splits period into N folds, sweeps on train, tests top-N configs on adjacent test fold. Side-by-side train-vs-test table with held/weakened/collapsed color coding on each cell. Summary "hold rate" = % of configs that still beat benchmark out-of-sample.
- [x] **Forward-test harness** — `backtester/forward.py` runs any preset + sizing config forward from a start date, persisting portfolios / trades / equity snapshots to DuckDB (`paper_portfolio`, `paper_trade`, `paper_equity`). Daily-idempotent: calling `advance_portfolio(id)` re-runs [started_at, as_of] and overwrites — safe to call on every refresh. `/paper/create`, `/paper/advance`, `/paper/advance-all`, `/paper`, `/paper/:id`, `/paper/:id DELETE`.
- [x] **Svelte `/paper` page** — portfolio table with equity/return/trade counts, per-row "Advance → today" button, detail view with equity curve + trade history, new-portfolio form seeded with champion config. Smoke-tested: "champion" (strength_weighted, hold=5, stop=3%, tgt=10%) from 2024-01-01 → today = **$100k → $321,992 (+222.0%) across 4,906 paper trades**.
- [ ] t-stat of alpha + factor attribution (Fama-French 3/5 factor)

## Signal library (plugs into pillars 2 & 3)

Signals sorted by alpha-per-effort. Each one is its own ingest + Svelte route,
all share the same DuckDB + event-study component pattern we built for Form 4.

### Event-driven (highest ROI)
- [x] **SC 13D activism tracker** — scraped via EFTS metadata (`providers/thirteen_d.py`), day-level cached. `/activism` route with issuer/filer leaderboards + filing feed + click-through to price modal. Backtester preset `schedule_13d`. Smoke test: 118 filings over 1 week, Starboard/GAMCO/Luminus detected.
- [x] **8-K material events feed** — one scraper for ALL items via `/events` route with chip filters (restatement 4.02, exec departure 5.02, auditor 4.01, bankruptcy 1.03, M&A 1.01/2.01, buyback/other 8.01, etc.). Bull/bear tone coloring. Smoke test: 611 filings / 535 issuers in 3 days, 25 item types active.
- [x] **NT 10-K / NT 10-Q late-filing flag** — `/late-filings` route, repeat-offender highlight, backtester short preset. Smoke test: 483 filings / 465 issuers over 6 weeks, KINETIC GROUP serial offender with 6 NTs.

### Coat-tail signals
- [x] **13F quarter-over-quarter deltas** — `/superinvestors` route, curated fund list (Buffett, Burry, Ackman, Tepper, Druckenmiller, Einhorn, Icahn, Tiger, Coatue, Viking, Third Point, ValueAct), NEW/LOAD/TRIM/EXIT taxonomy per position with click-through to price modal. Smoke test: Berkshire Q4 2025 portfolio $272.9B, 3 new positions including NYT.
- [x] **Form 144** — insider *intent* to sell (~30 days before execution). Live-scraped (no bulk), throttled, day-level cached. Integrated into insider-buying page as "Planned Sells" tab. Smoke test: 88 filings / $150M over 2 days (TVTX 5 officers, FDX $7.3M).
- [x] **SC 13G passive 5%+** — `/schedule-13g` dash + backtester preset `schedule_13g` (long).
- [x] **Form 3 new insiders** — `/form-3` dash + backtester preset `form3` (long).
- [x] **Form D private placements** — `/form-d` dash (microcap fundraising pulse).
- [x] **Form 15 / Form 25 delistings** — `/delistings` dash + backtester short preset `delistings`.
- [x] **20-F / 6-K foreign issuers** — `/foreign-issuers` dash. Opens ADR universe that the Form 4 pipeline doesn't cover.
- [x] **Position % of portfolio on sweep + walk-forward** — both pages now have the `fixed_usd` ↔ `pct_portfolio` toggle with slippage/commissions/gating inline.

### Dilution / capital structure
- [x] **S-3 / 424B secondary offerings** — `/secondaries` dash with repeat-offender highlight + backtester short preset `secondaries`.
- [x] **Tender offers** — `/tender-offers` dash (SC TO-T / TO-I / 14D9) + backtester long preset `tender_offers`.
- [ ] **S-8 stock-plan dilution** — accumulate and watch for inflection.
- [x] **IPO tracker + lockup expirations** — `providers/ipos.py` scrapes S-1 / S-1/A / 424B1/3/4 / 8-A via EFTS. `/ipos` page with Pipeline (S-1 filers without pricing, amendment count flags roadshow maturity), Recent (priced 424B4), and Lockups (180d post-pricing supply shock calendar). Ingest 240 days → 8,820 filings, 8 lockups hitting this week (TFC, JZ, LAES, BTCM, AVAH).
- [ ] **Form D private placements** — micro-cap fundraising pulse.

### Governance / risk
- [x] **DEF 14A proxy contests** — `/proxy-contests` dash + backtest preset. Scrapes DFAN14A / DEFC14A / PRRN14A / DFRN14A / PREN14A. Smoke: LULU (Lululemon) has a DFAN14A from 2026-04-16, real activist pressure visible in feed.
- [x] **8-K Item 4.01 auditor changes signal** — `auditor_changes` preset (short-side). Small window so far (Dec 2025 → today, 23 trades): return −4.2%, win rate 47.8%, alpha vs SPY −4.5%. Not yet a clear edge in this short bullish sample; infrastructure in place for when more 8-K history is ingested. Strength bonus when 4.01 is filed alone (vs bundled with many items).
- [ ] **Backfill form_8k to 2018+** — currently only has ~4 months from Dec 2025. Full backfill unlocks real backtests of auditor-change and other Item-level red flags.
- [ ] **10-K "going concern" language flag** — financial distress filter.
- [ ] **SC TO-T tender offers** — M&A live tape.

### Text-mining angle (needs an LLM or Loughran-McDonald dict)
- [ ] **10-K / 10-Q sentiment** — "uncertainty" / "litigious" word counts predict returns (McDonald 2011).
- [ ] **MD&A year-over-year diff** — big text changes often hide red flags.
- [ ] **Risk-factor additions** — new risks between filings; auto-highlight deltas.
- [ ] **10-K footnote NLP** — tax footnote, pension footnote, related-party disclosures.

### Under-exploited
- [ ] **NPORT-P monthly fund holdings** — far more granular than 13F, much less used by retail.
- [ ] **Auditor + counsel rotation tracking** — infrequent but meaningful.
- [x] **M&A announcements (DEFM14A / PREM14A / SC 13E-3)** — `providers/mergers.py` scrapes EFTS. `/mergers` page with by-target progression (preliminary → definitive → going-private pill), full filings feed. Live deal tape.
- [x] **Live IEX quotes (free via Alpaca)** — `providers/alpaca_live.py` fetches real-time bid/ask/last/spread/day-change via Alpaca's free IEX feed (`ALPACA_API_KEY_ID` + `ALPACA_API_SECRET`). `/live` page: watchlist + auto-polling (default 5s). IEX venue only (~2-3% of volume), but genuinely real-time and $0.
- [x] **Quant rigor bundle (9 stats)** — HAC/Newey-West Sharpe (3.31 vs IID 3.18 on champion), Hurst exponent (0.608 = trending), Deflated Sharpe Ratio (Bailey-LdP, p=99.99% single-trial; sweep-aware n_trials), regime-conditional Sharpe (up 3.56 / **down 5.89** — signal is counter-cyclical), Fama-French 5-factor (RMW+CMA added), Monte Carlo equity paths (1000-path block bootstrap, 1y terminal p5/p50/p95), Kelly fractional sizing mode, Information Coefficient per horizon (cluster_buys = +0.024 at 3-5d **then −0.056 at 60d** — strong reversion), PBO via CSCV (`/backtest/pbo` endpoint). All flow through `/backtest/run` summary; sweep now tags each combo with its own DSR.
- [ ] **Issuer buybacks (SC 13E4F + 8-K buyback authorizations)** — buyback announcements have well-documented forward-return alpha. Derivable from our 8-K Item 8.01 data plus 13E4F.
- [ ] **8-K Item 5.08 shareholder nominations** — signals imminent activism, often precedes 13D filing.
- [ ] **Rule 10b5-1 trading plan adoption** — pre-announced insider trading plans (8-K Item 5.02/5.07 + Form 144 disclosure). Adoption date + selling schedule is public in advance.
- [x] **Off-exchange flow tracker (ATS dark pools + non-ATS wholesalers)** — `providers/finra_ats.py` parameterized by venue_type. Two tables: `dark_pool_weekly` (ATS) + `non_ats_weekly` (OTC wholesaler / retail flow internalization by Citadel Securities / Virtu / Jane Street). `/dark-pool` page with tab switcher between ATS and non-ATS views, top-50 leaderboard, spike detection vs 8-week median, per-ticker time series + per-venue breakdown. Ingested 5 months (2025-11 → 2026-04) for both feeds; 8-week overlap for cross-feed analysis.
- [x] **Smart vs dumb money** — `/smart-vs-dumb` page cross-joins ATS + non-ATS + insider Form 4 net $ per ticker per week. Smart leaderboard = high ATS share + insider buys (institutional accumulation). Dumb = retail-flow dominant + insider sells (distribution warning). Live data showed FANG insiders sold $2.16B into retail wholesaler flow week of 2026-03-09; WMT −$345M; APP 169 insider sells into $5.08B retail routing.
- [x] **FINRA daily short-sale volume** — `providers/finra_short.py` + `short_sale_daily` table. Pulls CNMSshvol consolidated daily file from FINRA CDN. `/short-sale` page: latest-day top short %, spike detection vs 20-day median, per-ticker series. 11,371 tickers / day, universe avg short % ≈ 47%.
- [x] **Short-pressure bundle (FTDs + Reg SHO Threshold)** — `providers/sec_ftd.py` + `providers/finra_short_meta.py`. SEC bi-monthly FTD ZIPs parsed into `fails_to_deliver` (229k rows/2 months). FINRA thresholdList endpoint → `reg_sho_threshold` (12 tickers on 2026-04-17 — CERO Therapeutics). `/short-pressure` page with FTD / Threshold / Short Interest tabs. Note: FINRA's public `equityShortInterest` endpoint is frozen at 2021; ongoing short-interest ingest is blocked by FINRA moving to paid distribution.

### From Form 4 (pillar 2 extension)
- [ ] **Cluster insider-buying** — ≥2 insiders, ≥1 C-suite, bonus for small-cap — highest-signal subset of what the dashboard already shows.

## Infra / quality
- [x] **Scheduled daily refresh script** — `scripts/daily_refresh.py` pulls latest DERA quarters (180d lookback), refreshes EFTS scrapes (form144 / 13D / 13G / form3 / formD / delistings / foreign-filings / secondaries / tender-offers / proxy-contests / late-filings / 8-K), advances all active paper portfolios. Flags: `--days`, `--as-of`, `--skip-dera`, `--skip-scrape`, `--skip-paper`. Wire to cron/launchd (example in script header). Dry-test: paper-advance-only completed both portfolios in 23s.
- [ ] `httpxthrottlecache` on any remaining live-scrape path
- [ ] Tests: `data_store.py` roundtrip on a fixture ZIP
- [ ] Decide fate of `insider_screener.py` (keep as scraper fallback, or delete)
- [ ] Additional price-provider fallbacks beyond yfinance + Tiingo (wire any/all opt-in via env var):
      - **Alpha Vantage** — 25 req/day free, `ALPHAVANTAGE_API_KEY`
      - **Financial Modeling Prep** — 250 req/day free, `FMP_API_KEY`
      - **Polygon.io** — 5 req/min free, `POLYGON_API_KEY`
      - **EODHD** — limited demo, paid beyond

<script lang="ts">
	import { untrack } from 'svelte';
	import { Line, Bar } from 'svelte-chartjs';
	import {
		Chart as ChartJS,
		LineElement,
		LineController,
		BarElement,
		BarController,
		PointElement,
		CategoryScale,
		LinearScale,
		TimeScale,
		Tooltip,
		Legend,
		Filler
	} from 'chart.js';
	import 'chartjs-adapter-date-fns';

	ChartJS.register(
		LineElement,
		LineController,
		BarElement,
		BarController,
		PointElement,
		CategoryScale,
		LinearScale,
		TimeScale,
		Tooltip,
		Legend,
		Filler
	);

	type Trade = {
		ticker: string;
		signal_date: string;
		entry_date: string;
		entry_price: number;
		exit_date: string;
		exit_price: number;
		exit_reason: 'time' | 'stop' | 'target' | 'trailing';
		direction: number;
		shares: number;
		position_value: number;
		pnl: number;
		return_pct: number;
		label: string;
	};

	type EquityRow = { date: string; equity: number; cash: number; invested: number };
	type BenchRow = { date: string; equity: number };

	type Preset = { key: string; label: string };

	type FactorAttribution = {
		alpha_daily: number;
		alpha_annualized: number;
		alpha_t: number;
		alpha_se_daily: number;
		alpha_significant: boolean;
		factors: Record<
			string,
			{ beta: number; se: number; t: number; significant: boolean }
		>;
		r_squared: number;
		n_obs: number;
		residual_std_daily: number;
		factor_date_range: { start: string; end: string };
	};

	type GarchFit = {
		coefficients: { mu: number; omega: number; alpha: number; beta: number };
		persistence: number;
		half_life_days: number | null;
		unconditional_vol_ann: number;
		current_conditional_vol_ann: number;
		forecast_vol_ann: number | null;
		forecast_horizon_days: number;
		conditional_vol: Array<{ date: string; sigma_ann: number }>;
		n_obs: number;
		loglik: number;
		aic: number;
		bic: number;
	};

	type BacktestResponse = {
		preset: Preset;
		params: Record<string, string | number>;
		summary: Record<string, unknown>;
		equity_curve: EquityRow[];
		benchmark_curve: BenchRow[] | null;
		trades: Trade[];
		factor_attribution?: FactorAttribution | null;
		garch?: GarchFit | null;
	};

	// Defaults seeded to the best config from a full 36-combo parameter sweep.
	// cluster_buys · 2022-01-01 → 2024-12-31 · hold=10 · stop=5% · target=15%
	// → Sharpe 2.74, alpha +38.2% vs SPY (thousands of trades across 10,535 signals).
	// Supersedes the earlier hold=20/stop=8/target=20 seed (Sharpe 1.53, +18.4%).

	// Champion config from the 27-combo sweep of the new sizing modes over
	// cluster_buys · 2022-01-01 → 2024-12-31:
	//   strength_weighted · hold=5 · stop=3% · target=10% · base=2% · cap=3×
	//   → Sharpe 3.18 · return +302% · alpha +279% vs SPY
	// (NB: 27.1% survivorship gap — 2,851 signals on delisted names are
	// excluded until a TIINGO_API_KEY is set. The real Sharpe is lower.)
	let presets = $state<Preset[]>([]);
	let preset = $state('cluster_buys');
	let start = $state('2022-01-01');
	let end = $state('2024-12-31');
	let holdDays = $state(5);
	let positionMode = $state<
		| 'fixed_usd'
		| 'pct_portfolio'
		| 'risk_per_trade'
		| 'vol_target'
		| 'strength_weighted'
		| 'target_deployment'
	>('strength_weighted');
	let positionSize = $state(10000);
	let positionPctPct = $state(2);
	let riskPerTradePct = $state(1);    // risk_per_trade: 1% of equity risked per trade
	let targetVolPct = $state(10);      // vol_target: 10% annualised vol per position
	let targetDeploymentPctPct = $state(80); // target_deployment: 80% target invested
	let strengthScaleCap = $state(3);   // strength_weighted: clamp multiplier to [1/cap, cap]
	let maxSignalsPerDay = $state(0);   // 0 = uncapped
	let maxSectorPctPct = $state(0);    // 0 = off, else cap % per sector
	let correlationAdjust = $state(false);
	let correlationShrinkagePct = $state(50); // 50% = full shrink on ρ=1
	let correlationLookback = $state(60);
	let benchmark = $state('SPY');
	let stopLossPct = $state(3);
	let profitTargetPct = $state(10);
	let trailingStopPct = $state(0);
	// Execution realism — defaults chosen to reflect a realistic retail book.
	let slippageBps = $state(10);            // 10 bps each side
	let commissionPerTrade = $state(1);      // $1 per fill
	let onePositionPerTicker = $state(false);

	let data = $state<BacktestResponse | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	type BaselineIter = {
		seed: number;
		n_trades: number;
		sharpe: number;
		total_return_pct: number;
		max_drawdown_pct: number;
	};
	type BaselineResponse = {
		preset: string;
		label: string;
		n_signals: number;
		universe_size: number;
		real: {
			sharpe: number;
			total_return_pct: number;
			max_drawdown_pct: number;
			n_trades: number;
		};
		random: {
			n_iterations: number;
			median: number;
			p5: number;
			p95: number;
			mean: number;
			iterations: BaselineIter[];
		};
		p_value: number;
		interpretation: string;
	};
	let baselineIterations = $state(20);
	let baselineData = $state<BaselineResponse | null>(null);
	let baselineLoading = $state(false);
	let baselineError = $state<string | null>(null);

	async function loadPresets() {
		const r = await fetch('/api/backtest/presets');
		if (r.ok) presets = (await r.json()).presets ?? [];
	}
	// Boot: load preset list and auto-run the default (champion config from the
	// full 36-combo sweep) so users land on results instead of an empty page.
	// Uses untracked() so edits to start/end/etc. don't kick off re-runs.
	$effect(() => {
		untrack(() => {
			loadPresets();
			run();
		});
	});

	// Clamp a possibly-empty/NaN number input to [lo, hi] with a fallback so
	// blank fields don't punt the user into API validation errors.
	function clamp(v: unknown, lo: number, hi: number, fallback: number): number {
		const n = typeof v === 'number' ? v : Number(v);
		if (!Number.isFinite(n)) return fallback;
		return Math.max(lo, Math.min(hi, n));
	}

	async function run() {
		// Client-side validation — surface a helpful message before hitting the API
		if (!start || !end) {
			error = 'Pick a start and end date.';
			return;
		}
		if (end <= start) {
			error = `End (${end}) must be after start (${start}).`;
			return;
		}
		// Sanitize every numeric field. User can clear any input and defaults
		// take over instead of the API returning validation errors.
		holdDays = clamp(holdDays, 1, 252, 5);
		positionSize = clamp(positionSize, 100, 10_000_000, 10_000);
		positionPctPct = clamp(positionPctPct, 0.01, 100, 2);
		riskPerTradePct = clamp(riskPerTradePct, 0.01, 50, 1);
		targetVolPct = clamp(targetVolPct, 1, 100, 10);
		targetDeploymentPctPct = clamp(targetDeploymentPctPct, 0, 100, 80);
		strengthScaleCap = clamp(strengthScaleCap, 1, 10, 3);
		maxSignalsPerDay = clamp(maxSignalsPerDay, 0, 200, 0);
		maxSectorPctPct = clamp(maxSectorPctPct, 0, 100, 0);
		stopLossPct = clamp(stopLossPct, 0, 50, 0);
		profitTargetPct = clamp(profitTargetPct, 0, 500, 0);
		trailingStopPct = clamp(trailingStopPct, 0, 50, 0);
		slippageBps = clamp(slippageBps, 0, 500, 0);
		commissionPerTrade = clamp(commissionPerTrade, 0, 100, 0);
		correlationShrinkagePct = clamp(correlationShrinkagePct, 0, 100, 50);
		correlationLookback = clamp(correlationLookback, 10, 252, 60);
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				preset,
				start,
				end,
				hold_days: String(holdDays),
				position_mode: positionMode,
				position_size: String(positionSize),
				position_pct: String(positionPctPct / 100),
				benchmark,
				stop_loss_pct: String(stopLossPct / 100),
				profit_target_pct: String(profitTargetPct / 100),
				trailing_stop_pct: String(trailingStopPct / 100),
				slippage_bps: String(slippageBps),
				commission_per_trade: String(commissionPerTrade),
				one_position_per_ticker: String(onePositionPerTicker),
				risk_per_trade_pct: String(riskPerTradePct / 100),
				target_vol_pct: String(targetVolPct / 100),
				target_deployment_pct: String(targetDeploymentPctPct / 100),
				strength_scale_cap: String(strengthScaleCap),
				max_signals_per_day: String(maxSignalsPerDay),
				max_sector_pct: String(maxSectorPctPct / 100),
				correlation_adjust: String(correlationAdjust),
				correlation_shrinkage: String(correlationShrinkagePct / 100),
				correlation_lookback: String(correlationLookback)
			});
			const r = await fetch(`/api/backtest/run?${params}`);
			if (!r.ok) {
				let detail = `${r.status} ${r.statusText}`;
				try {
					const body = await r.json();
					if (Array.isArray(body?.detail)) {
						detail =
							body.detail
								.map(
									(d: { loc?: string[]; msg?: string }) =>
										`${(d.loc ?? []).slice(-1)[0] ?? '?'}: ${d.msg ?? 'invalid'}`
								)
								.join(' · ') || detail;
					} else if (body?.detail) {
						detail = String(body.detail);
					} else if (body?.error) {
						detail = String(body.error);
					}
				} catch {
					// non-JSON body, keep status text
				}
				throw new Error(detail);
			}
			data = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			data = null;
		} finally {
			loading = false;
		}
	}

	async function runBaseline() {
		if (!data) {
			baselineError = 'Run a backtest first so the baseline uses matching params.';
			return;
		}
		baselineLoading = true;
		baselineError = null;
		try {
			const params = new URLSearchParams({
				preset,
				start,
				end,
				n_iterations: String(baselineIterations),
				hold_days: String(holdDays),
				stop_loss_pct: String(stopLossPct / 100),
				profit_target_pct: String(profitTargetPct / 100),
				position_mode: positionMode,
				position_size: String(positionSize),
				position_pct: String(positionPctPct / 100),
				slippage_bps: String(slippageBps),
				commission_per_trade: String(commissionPerTrade)
			});
			const r = await fetch(`/api/backtest/random-baseline?${params}`);
			if (!r.ok) {
				const body = await r.json().catch(() => ({}));
				throw new Error(body?.detail ?? body?.error ?? `${r.status} ${r.statusText}`);
			}
			const body = await r.json();
			if (body.error) throw new Error(body.error);
			baselineData = body;
		} catch (e) {
			baselineError = e instanceof Error ? e.message : String(e);
			baselineData = null;
		} finally {
			baselineLoading = false;
		}
	}

	const baselineChart = $derived.by(() => {
		if (!baselineData) return null;
		const iters = baselineData.random.iterations;
		const real = baselineData.real.sharpe;
		const labels = iters.map((_, i) => `#${i + 1}`).concat(['Real']);
		const randomBars = iters.map((it) => it.sharpe).concat([null as unknown as number]);
		const realBars = iters.map(() => null as unknown as number).concat([real]);
		return {
			labels,
			datasets: [
				{
					label: 'Random permutations',
					data: randomBars,
					backgroundColor: '#6b7280',
					borderColor: '#6b7280',
					borderWidth: 0
				},
				{
					label: 'Real signal',
					data: realBars,
					backgroundColor: '#22c55e',
					borderColor: '#22c55e',
					borderWidth: 0
				}
			]
		};
	});

	const deploymentStats = $derived.by(() => {
		if (!data?.equity_curve?.length) return null;
		const rows = data.equity_curve.map((r) => ({
			date: r.date,
			pct: r.equity > 0 ? (r.invested / r.equity) * 100 : 0
		}));
		const vals = rows.map((r) => r.pct);
		const avg = vals.reduce((a, b) => a + b, 0) / Math.max(1, vals.length);
		const peak = Math.max(...vals);
		const days_over_50 = vals.filter((v) => v >= 50).length;
		return { rows, avg, peak, days_over_50, n_days: vals.length };
	});

	const deploymentChart = $derived.by(() => {
		const s = deploymentStats;
		if (!s) return null;
		return {
			datasets: [
				{
					label: '% capital invested',
					data: s.rows.map((r) => ({ x: r.date, y: r.pct })),
					borderColor: '#a78bfa',
					backgroundColor: 'rgba(167, 139, 250, 0.15)',
					borderWidth: 1.5,
					pointRadius: 0,
					tension: 0.1,
					fill: true
				}
			]
		};
	});

	const equityChart = $derived.by(() => {
		if (!data?.equity_curve?.length) return null;
		const base = data.equity_curve[0].equity || 1;
		const datasets: Array<{ label: string; data: { x: string; y: number }[]; borderColor: string; backgroundColor: string; borderWidth: number; pointRadius: number; tension: number; fill?: boolean }> = [
			{
				label: data.preset.label,
				data: data.equity_curve.map((r) => ({ x: r.date, y: (r.equity / base - 1) * 100 })),
				borderColor: '#60a5fa',
				backgroundColor: 'rgba(96, 165, 250, 0.12)',
				borderWidth: 2,
				pointRadius: 0,
				tension: 0.1,
				fill: true
			}
		];
		if (data.benchmark_curve?.length) {
			const bbase = data.benchmark_curve[0].equity || 1;
			datasets.push({
				label: `Benchmark (${data.params.benchmark})`,
				data: data.benchmark_curve.map((r) => ({ x: r.date, y: (r.equity / bbase - 1) * 100 })),
				borderColor: '#f59e0b',
				backgroundColor: 'transparent',
				borderWidth: 1.5,
				pointRadius: 0,
				tension: 0.1
			});
		}
		return { datasets };
	});

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const chartOptions: any = {
		responsive: true,
		maintainAspectRatio: false,
		interaction: { mode: 'nearest', intersect: false, axis: 'x' },
		plugins: {
			legend: { labels: { color: '#e2e8f0' } },
			tooltip: {
				callbacks: {
					label: (ctx: { dataset: { label?: string }; parsed: { y: number } }) => {
						const v = ctx.parsed.y;
						return `${ctx.dataset.label}: ${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
					}
				}
			}
		},
		scales: {
			x: {
				type: 'time',
				time: { unit: 'week', displayFormats: { week: 'MMM d' } },
				ticks: { color: '#94a3b8' },
				grid: { color: '#1e293b' }
			},
			y: {
				ticks: {
					color: '#94a3b8',
					callback: (v: number | string) => `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`
				},
				grid: { color: '#1e293b' }
			}
		}
	};

	const fmtPct = (v: unknown) =>
		typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : '—';
	const fmtMoney = (v: unknown) =>
		typeof v === 'number'
			? v >= 1e6
				? `$${(v / 1e6).toFixed(2)}M`
				: `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
			: '—';
	const fmtNum = (v: unknown, digits = 2) =>
		typeof v === 'number' ? v.toFixed(digits) : '—';

	const summary = $derived(data?.summary ?? {});
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const bench = $derived((summary as any)?.benchmark ?? null);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const alpha = $derived((summary as any)?.alpha_vs_benchmark_pct ?? null);
</script>

<h1>🧪 Backtest <a class="sweep-link" href="/backtest/sweep">→ parameter sweep</a> <a class="sweep-link" href="/backtest/walkforward">→ walk-forward</a></h1>
<p class="subtitle">
	Plug any SEC-derived signal into a daily-bar event-driven engine. Entry at the
	next trading day's close, fixed N-day hold, equal-$ position sizing, compared
	against a buy-and-hold benchmark.
</p>

<section class="controls">
	<label>
		<span>Signal</span>
		<select bind:value={preset}>
			{#each presets as p}
				<option value={p.key}>{p.label}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Start</span>
		<input type="date" bind:value={start} />
	</label>
	<label>
		<span>End</span>
		<input type="date" bind:value={end} />
	</label>
	<label>
		<span>Max hold (days)</span>
		<input type="number" min="1" max="252" bind:value={holdDays} />
	</label>
	<label>
		<span>Sizing mode</span>
		<select bind:value={positionMode}>
			<option value="fixed_usd">Fixed $</option>
			<option value="pct_portfolio">% of portfolio</option>
			<option value="risk_per_trade">Risk / trade</option>
			<option value="vol_target">Vol target</option>
			<option value="strength_weighted">Strength weighted</option>
			<option value="target_deployment">Target deployment</option>
		</select>
	</label>
	{#if positionMode === 'fixed_usd'}
		<label>
			<span>Position $</span>
			<input type="number" min="100" step="1000" bind:value={positionSize} required />
		</label>
	{:else if positionMode === 'pct_portfolio'}
		<label>
			<span>Position % of equity</span>
			<input type="number" min="0.1" max="100" step="0.5" bind:value={positionPctPct} required />
		</label>
	{:else if positionMode === 'risk_per_trade'}
		<label title="Risk per trade as % of equity. Needs stop-loss. Position = (risk% × equity) / stop%.">
			<span>Risk / trade %</span>
			<input type="number" min="0.1" max="50" step="0.25" bind:value={riskPerTradePct} />
		</label>
	{:else if positionMode === 'vol_target'}
		<label title="Per-position annualised volatility target. Vol is estimated from price history.">
			<span>Target vol %</span>
			<input type="number" min="1" max="100" step="1" bind:value={targetVolPct} />
		</label>
	{:else if positionMode === 'strength_weighted'}
		<label title="Base % of equity for a median-strength signal. Actual size scales with signal.strength / median_strength.">
			<span>Base position %</span>
			<input type="number" min="0.1" max="100" step="0.5" bind:value={positionPctPct} required />
		</label>
		<label title="Clamps the strength multiplier to [1/cap, cap] so outliers don't dominate.">
			<span>Strength cap</span>
			<input type="number" min="1" max="10" step="0.5" bind:value={strengthScaleCap} />
		</label>
	{:else}
		<label title="Keep portfolio at this % invested. New signals size to close the gap.">
			<span>Target invested %</span>
			<input type="number" min="10" max="100" step="5" bind:value={targetDeploymentPctPct} />
		</label>
		<label title="Per-position cap as % of equity — prevents one signal hogging the book.">
			<span>Max position %</span>
			<input type="number" min="0.5" max="100" step="0.5" bind:value={positionPctPct} />
		</label>
	{/if}
	<label title="Cap bets per signal-day; top N by signal strength. 0 = uncapped.">
		<span>Max signals/day</span>
		<input type="number" min="0" max="200" step="1" bind:value={maxSignalsPerDay} />
	</label>
	<label title="Cap total portfolio exposure per GICS sector (0 = off). Requires yfinance sector lookup.">
		<span>Max sector %</span>
		<input type="number" min="0" max="100" step="5" bind:value={maxSectorPctPct} />
	</label>
	<label class="checkbox" title="Shrink new positions that are correlated with currently-open positions.">
		<input type="checkbox" bind:checked={correlationAdjust} />
		<span>ρ-adjust size</span>
	</label>
	{#if correlationAdjust}
		<label title="0 = off, 100 = fully shrink on ρ=1. 50 = a fully-correlated add gets half-sized.">
			<span>ρ shrinkage %</span>
			<input type="number" min="0" max="100" step="10" bind:value={correlationShrinkagePct} />
		</label>
		<label title="Rolling window (days) for return correlation.">
			<span>ρ lookback</span>
			<input type="number" min="10" max="252" step="10" bind:value={correlationLookback} />
		</label>
	{/if}
	<label>
		<span>Stop-loss %</span>
		<input type="number" min="0" max="50" step="1" bind:value={stopLossPct} title="0 = disabled" />
	</label>
	<label>
		<span>Profit target %</span>
		<input
			type="number"
			min="0"
			max="500"
			step="5"
			bind:value={profitTargetPct}
			title="0 = disabled"
		/>
	</label>
	<label>
		<span>Trailing stop %</span>
		<input
			type="number"
			min="0"
			max="50"
			step="1"
			bind:value={trailingStopPct}
			title="0 = disabled"
		/>
	</label>
	<label title="Basis points applied at entry and exit fills (10 bps ≈ 0.10%)">
		<span>Slippage (bps)</span>
		<input type="number" min="0" max="500" step="1" bind:value={slippageBps} />
	</label>
	<label title="Flat $ commission per fill (2× per trade)">
		<span>Commission $/trade</span>
		<input type="number" min="0" max="100" step="0.5" bind:value={commissionPerTrade} />
	</label>
	<label class="checkbox" title="Skip duplicate signals on a ticker while one is already open">
		<input type="checkbox" bind:checked={onePositionPerTicker} />
		<span>One position per ticker</span>
	</label>
	<label>
		<span>Benchmark</span>
		<input type="text" bind:value={benchmark} size="5" />
	</label>
	<button onclick={run} disabled={loading}>
		{loading ? 'Running…' : 'Run'}
	</button>
</section>

{#if error}
	<div class="error">Error: {error}</div>
{/if}

{#if loading}
	<div class="status">Running backtest… fetching prices and simulating…</div>
{:else if data}
	{@const nSig = (summary as { n_signals?: number }).n_signals ?? 0}
	{@const nTr = (summary as { n_trades?: number }).n_trades ?? 0}
	{#if nSig === 0}
		<div class="warning">
			The <strong>{data.preset.label}</strong> signal returned 0 hits for {start} → {end}.
			{#if preset === 'form144_cluster_sells'}
				Form 144 data is live-scraped and only covers dates previously loaded via the
				<a href="/insider-buying">Planned Sells tab</a>. Pick a narrow window you've already loaded, or visit that tab first to scrape a longer history.
			{:else}
				Try widening the window, or lowering the signal thresholds (see
				<code>backtester/signals.py</code>).
			{/if}
		</div>
	{:else if nTr === 0}
		<div class="warning">
			{nSig} signal{nSig === 1 ? '' : 's'} found but zero trades opened — likely all skipped for
			insufficient bars (window ends too close to the signal). Extend End date past the hold
			period and retry.
		</div>
	{/if}

	<section class="metrics">
		<div class="metric">
			<span class="metric-label">Trades</span>
			<span class="metric-value">{(summary as { n_trades?: number }).n_trades ?? 0}</span>
			{#if (summary as { max_concurrent_positions?: number }).max_concurrent_positions}
				<span class="ci" title="Peak number of positions open simultaneously">
					peak {(summary as { max_concurrent_positions: number }).max_concurrent_positions} open
				</span>
			{/if}
		</div>
		<div class="metric">
			<span class="metric-label">Total return</span>
			<span class="metric-value" class:up={Number((summary as { total_return_pct?: number }).total_return_pct ?? 0) >= 0} class:down={Number((summary as { total_return_pct?: number }).total_return_pct ?? 0) < 0}>
				{fmtPct((summary as { total_return_pct?: number }).total_return_pct)}
			</span>
		</div>
		<div class="metric">
			<span class="metric-label">CAGR</span>
			<span class="metric-value">{fmtPct((summary as { cagr?: number }).cagr)}</span>
		</div>
		<div class="metric">
			<span class="metric-label">Sharpe</span>
			<span class="metric-value">{fmtNum((summary as { sharpe?: number }).sharpe)}</span>
			{#if (summary as { sharpe_ci?: { p5: number; p95: number; n_samples: number } }).sharpe_ci?.n_samples}
				{@const ci = (summary as { sharpe_ci: { p5: number; p95: number } }).sharpe_ci}
				<span class="ci" title="Bootstrap 95% confidence interval (1000 resamples)">
					95% CI [{fmtNum(ci.p5)} — {fmtNum(ci.p95)}]
				</span>
			{/if}
		</div>
		{#if (summary as { calmar?: number | null }).calmar != null}
			<div class="metric">
				<span class="metric-label">Calmar</span>
				<span class="metric-value">{fmtNum((summary as { calmar?: number }).calmar)}</span>
			</div>
		{/if}
		{#if (summary as { var_95?: { historical_var: number; cvar: number; cornish_fisher_var?: number; skewness?: number; excess_kurtosis?: number } }).var_95}
			{@const v = (summary as { var_95: { historical_var: number; cvar: number; cornish_fisher_var?: number; skewness?: number; excess_kurtosis?: number } }).var_95}
			<div class="metric">
				<span class="metric-label">95% VaR (daily)</span>
				<span class="metric-value down">{fmtPct(v.historical_var)}</span>
				<span class="ci" title="Expected Shortfall (CVaR): mean of worst 5% of days">
					ES {fmtPct(v.cvar)}
				</span>
			</div>
			{#if v.cornish_fisher_var != null}
				<div class="metric">
					<span class="metric-label" title="Cornish-Fisher VaR: adjusts the Normal quantile for sample skew + excess kurtosis. Gets more negative than parametric VaR when returns have heavy left tails.">
						CF-VaR (heavy tails)
					</span>
					<span class="metric-value down">{fmtPct(v.cornish_fisher_var)}</span>
					{#if v.skewness != null && v.excess_kurtosis != null}
						<span class="ci">skew {fmtNum(v.skewness)} · exK {fmtNum(v.excess_kurtosis)}</span>
					{/if}
				</div>
			{/if}
		{/if}
		<div class="metric">
			<span class="metric-label">Max drawdown</span>
			<span class="metric-value down">{fmtPct((summary as { max_drawdown_pct?: number }).max_drawdown_pct)}</span>
			{#if (summary as { max_drawdown_duration_days?: number }).max_drawdown_duration_days}
				<span class="ci" title="Longest continuous stretch below all-time running peak">
					{(summary as { max_drawdown_duration_days: number }).max_drawdown_duration_days}d longest underwater
					{#if (summary as { current_underwater_days?: number }).current_underwater_days}
						· {(summary as { current_underwater_days: number }).current_underwater_days}d now
					{/if}
				</span>
			{/if}
		</div>
		<div class="metric">
			<span class="metric-label">Win rate</span>
			<span class="metric-value">{fmtPct((summary as { win_rate?: number }).win_rate)}</span>
		</div>
		<div class="metric">
			<span class="metric-label">Avg return / trade</span>
			<span class="metric-value">{fmtPct((summary as { avg_return_pct?: number }).avg_return_pct)}</span>
		</div>
		{#if bench}
			<div class="metric">
				<span class="metric-label">{data.params.benchmark}</span>
				<span class="metric-value">{fmtPct(bench.total_return_pct)}</span>
			</div>
			<div class="metric" class:up={Number(alpha ?? 0) >= 0} class:down={Number(alpha ?? 0) < 0}>
				<span class="metric-label">Alpha</span>
				<span class="metric-value" class:up={Number(alpha ?? 0) >= 0} class:down={Number(alpha ?? 0) < 0}>
					{alpha != null ? (Number(alpha) >= 0 ? '+' : '') + fmtPct(alpha) : '—'}
				</span>
			</div>
			{#if (summary as { information_ratio?: number; tracking_error_ann?: number }).information_ratio != null}
				{@const ir = (summary as { information_ratio: number }).information_ratio}
				{@const te = (summary as { tracking_error_ann?: number }).tracking_error_ann}
				<div class="metric" class:up={ir >= 0} class:down={ir < 0}>
					<span class="metric-label" title="Information Ratio: annualized active return / tracking error vs benchmark">
						Info Ratio
					</span>
					<span class="metric-value" class:up={ir >= 0} class:down={ir < 0}>
						{fmtNum(ir)}
					</span>
					{#if te != null}
						<span class="ci">TE {fmtPct(te)}</span>
					{/if}
				</div>
			{/if}
		{/if}
		{#if (summary as { turnover?: { annualized_ratio: number; gross_notional_usd: number } }).turnover}
			{@const tv = (summary as { turnover: { annualized_ratio: number; gross_notional_usd: number } }).turnover}
			<div class="metric">
				<span class="metric-label" title="Annualized turnover = total gross notional traded (entry + exit) / avg equity / years">
					Turnover
				</span>
				<span class="metric-value">{fmtNum(tv.annualized_ratio)}×</span>
				<span class="ci">${(tv.gross_notional_usd / 1e6).toFixed(1)}M traded</span>
			</div>
		{/if}
		{#if (summary as { costs?: { total_cost_usd: number; slippage_usd: number; commission_usd: number; cost_drag_pct_of_gross: number } }).costs}
			{@const c = (summary as { costs: { total_cost_usd: number; slippage_usd: number; commission_usd: number; cost_drag_pct_of_gross: number } }).costs}
			<div class="metric">
				<span class="metric-label" title="Transaction Cost Attribution: $ spent on slippage + commission, and the fraction of gross profit those costs ate.">
					Costs
				</span>
				<span class="metric-value down">−${(c.total_cost_usd / 1e3).toFixed(1)}k</span>
				<span class="ci">
					slip ${(c.slippage_usd / 1e3).toFixed(1)}k · fees ${(c.commission_usd / 1e3).toFixed(1)}k
					· {(c.cost_drag_pct_of_gross * 100).toFixed(1)}% drag
				</span>
			</div>
		{/if}
	</section>

	<section class="panel">
		<div class="baseline-header">
			<h2>
				Baseline test
				<span class="panel-sub">— permutation null: shuffle ticker↔date pairings, rerun K times</span>
			</h2>
			<div class="baseline-controls">
				<label>
					Iterations
					<input
						type="number"
						min="5"
						max="200"
						step="5"
						bind:value={baselineIterations}
					/>
				</label>
				<button onclick={runBaseline} disabled={baselineLoading}>
					{baselineLoading ? 'Running…' : 'Run baseline'}
				</button>
			</div>
		</div>
		{#if baselineError}
			<div class="error">{baselineError}</div>
		{/if}
		{#if baselineData}
			{@const bd = baselineData}
			<div class="baseline-stats">
				<div class="metric">
					<span class="metric-label">Real Sharpe</span>
					<span class="metric-value up">{fmtNum(bd.real.sharpe)}</span>
				</div>
				<div class="metric">
					<span class="metric-label">Random median</span>
					<span class="metric-value">{fmtNum(bd.random.median)}</span>
				</div>
				<div class="metric">
					<span class="metric-label">Random 95% band</span>
					<span class="metric-value">
						[{fmtNum(bd.random.p5)} — {fmtNum(bd.random.p95)}]
					</span>
				</div>
				<div class="metric">
					<span class="metric-label">p-value</span>
					<span
						class="metric-value"
						class:up={bd.p_value < 0.05}
						class:down={bd.p_value >= 0.05}
					>
						{bd.p_value.toFixed(3)}
					</span>
				</div>
				<div class="metric">
					<span class="metric-label">Iterations</span>
					<span class="metric-value">{bd.random.n_iterations}</span>
				</div>
			</div>
			<p class="baseline-verdict" class:up={bd.p_value < 0.05} class:down={bd.p_value >= 0.05}>
				{bd.interpretation}
			</p>
			{#if baselineChart}
				<div class="chart">
					<!-- eslint-disable-next-line @typescript-eslint/no-explicit-any -->
					<Bar data={baselineChart as any} options={chartOptions as any} />
				</div>
			{/if}
		{:else if !baselineLoading}
			<p class="empty">
				Runs {baselineIterations}× with the same dates + ticker frequencies but randomised
				(ticker, date) pairings. If the real Sharpe exceeds the random p95, the specific
				filing-date-to-ticker coupling is the alpha — not the universe, not the window.
			</p>
		{/if}
	</section>

	{#if data.factor_attribution}
		{@const fa = data.factor_attribution}
		<section class="panel">
			<h2>
				Fama-French factor attribution
				<span class="panel-sub">
					— OLS of excess daily returns on Mkt-RF · SMB · HML · MOM
				</span>
			</h2>
			<div class="baseline-stats">
				<div class="metric">
					<span class="metric-label">Annualized alpha</span>
					<span
						class="metric-value"
						class:up={fa.alpha_annualized >= 0}
						class:down={fa.alpha_annualized < 0}
					>
						{(fa.alpha_annualized * 100).toFixed(2)}%
					</span>
					<span class="ci">t = {fa.alpha_t.toFixed(2)}{fa.alpha_significant ? ' ✓' : ''}</span>
				</div>
				<div class="metric">
					<span class="metric-label">R²</span>
					<span class="metric-value">{fa.r_squared.toFixed(3)}</span>
					<span class="ci">factors explain this much of variance</span>
				</div>
				<div class="metric">
					<span class="metric-label">Observations</span>
					<span class="metric-value">{fa.n_obs}</span>
				</div>
			</div>
			<table class="factor-table">
				<thead>
					<tr>
						<th>Factor</th>
						<th class="num">Beta</th>
						<th class="num">SE</th>
						<th class="num">t-stat</th>
						<th>Significant (|t|>2)</th>
					</tr>
				</thead>
				<tbody>
					{#each Object.entries(fa.factors) as [name, f]}
						<tr>
							<td>{name}</td>
							<td class="num" class:up={f.beta >= 0} class:down={f.beta < 0}>
								{f.beta >= 0 ? '+' : ''}{f.beta.toFixed(3)}
							</td>
							<td class="num">{f.se.toFixed(3)}</td>
							<td class="num">{f.t.toFixed(2)}</td>
							<td>{f.significant ? 'yes' : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			<p class="baseline-verdict" class:up={fa.alpha_significant} class:down={!fa.alpha_significant}>
				{#if fa.alpha_significant && fa.alpha_annualized > 0}
					Residual alpha is statistically significant — strategy earns real excess return
					after stripping out factor exposure.
				{:else if fa.alpha_significant && fa.alpha_annualized < 0}
					Negative alpha with |t| > 2 — strategy underperforms what its factor exposure would predict.
				{:else}
					Alpha is not statistically distinguishable from zero once factor exposures are accounted for.
				{/if}
			</p>
		</section>
	{/if}

	{#if deploymentStats}
		{@const ds = deploymentStats}
		<section class="panel">
			<h2>
				Capital deployment
				<span class="panel-sub">— % of equity invested vs sitting in cash</span>
			</h2>
			<div class="baseline-stats">
				<div class="metric">
					<span class="metric-label">Avg deployed</span>
					<span class="metric-value">{ds.avg.toFixed(1)}%</span>
					<span class="ci">higher = less cash drag</span>
				</div>
				<div class="metric">
					<span class="metric-label">Peak deployed</span>
					<span class="metric-value">{ds.peak.toFixed(1)}%</span>
				</div>
				<div class="metric">
					<span class="metric-label">Days ≥ 50% invested</span>
					<span class="metric-value">
						{ds.days_over_50} / {ds.n_days}
						<span class="ci">
							{((ds.days_over_50 / Math.max(1, ds.n_days)) * 100).toFixed(0)}%
						</span>
					</span>
				</div>
			</div>
			{#if deploymentChart}
				<div class="chart">
					<Line
						data={deploymentChart}
						options={{
							...chartOptions,
							plugins: { legend: { display: false } },
							scales: {
								...chartOptions.scales,
								y: {
									...chartOptions.scales.y,
									min: 0,
									ticks: {
										color: '#94a3b8',
										callback: (v: number | string) => `${Number(v).toFixed(0)}%`
									}
								}
							}
						}}
					/>
				</div>
			{/if}
			<p class="panel-sub" style="margin-top:0.5rem">
				Fixed-$ mode seeds starting cash to cover peak concurrent positions, so on most days the book runs well below peak — cash sits idle. Switch to <code>pct_portfolio</code> or raise <code>position_size</code> to shrink the drag, or raise <code>max_signals_per_day</code> to trade more aggressively.
			</p>
		</section>
	{/if}

	<section class="panel">
		<h2>Equity curve <span class="panel-sub">— % from starting equity</span></h2>
		{#if equityChart}
			<div class="chart"><Line data={equityChart} options={chartOptions} /></div>
		{:else}
			<p class="empty">No equity curve.</p>
		{/if}
	</section>

	{#if (summary as { underwater?: Array<{ date: string; drawdown_pct: number }> }).underwater}
		{@const uw = (summary as { underwater: Array<{ date: string; drawdown_pct: number }> }).underwater}
		<section class="panel">
			<h2>Drawdown <span class="panel-sub">— equity / running peak − 1 (underwater curve)</span></h2>
			<div class="chart">
				<Line
					data={{
						datasets: [
							{
								label: 'Drawdown',
								data: uw.map((r) => ({ x: r.date, y: r.drawdown_pct * 100 })),
								borderColor: '#f87171',
								backgroundColor: 'rgba(248, 113, 113, 0.25)',
								borderWidth: 1.5,
								pointRadius: 0,
								tension: 0.1,
								fill: true
							}
						]
					}}
					options={{
						...chartOptions,
						plugins: { legend: { display: false } },
						scales: {
							...chartOptions.scales,
							y: {
								...chartOptions.scales.y,
								ticks: {
									color: '#94a3b8',
									callback: (v: number | string) => `${Number(v).toFixed(1)}%`
								}
							}
						}
					}}
				/>
			</div>
		</section>
	{/if}

	{#if data.garch}
		{@const g = data.garch}
		<section class="panel">
			<h2>
				GARCH(1,1) conditional volatility
				<span class="panel-sub">
					— σ²_t = ω + α·ε²_{'{t-1}'} + β·σ²_{'{t-1}'} · {g.n_obs} obs · AIC {g.aic.toFixed(1)}
				</span>
			</h2>
			<div class="baseline-stats">
				<div class="metric">
					<span class="metric-label" title="α + β — closer to 1 means vol shocks decay slowly">Persistence</span>
					<span class="metric-value">{g.persistence.toFixed(3)}</span>
					{#if g.half_life_days}
						<span class="ci">half-life {g.half_life_days.toFixed(1)}d</span>
					{/if}
				</div>
				<div class="metric">
					<span class="metric-label">ω (base var)</span>
					<span class="metric-value">{g.coefficients.omega.toFixed(4)}</span>
				</div>
				<div class="metric">
					<span class="metric-label">α (shock weight)</span>
					<span class="metric-value">{g.coefficients.alpha.toFixed(3)}</span>
				</div>
				<div class="metric">
					<span class="metric-label">β (prior-vol weight)</span>
					<span class="metric-value">{g.coefficients.beta.toFixed(3)}</span>
				</div>
				<div class="metric">
					<span class="metric-label">Unconditional σ (ann)</span>
					<span class="metric-value">{(g.unconditional_vol_ann * 100).toFixed(1)}%</span>
				</div>
				<div class="metric">
					<span class="metric-label">Current σ (ann)</span>
					<span class="metric-value">{(g.current_conditional_vol_ann * 100).toFixed(1)}%</span>
				</div>
				{#if g.forecast_vol_ann != null}
					<div class="metric">
						<span class="metric-label">{g.forecast_horizon_days}-day forecast</span>
						<span class="metric-value">{(g.forecast_vol_ann * 100).toFixed(1)}%</span>
					</div>
				{/if}
			</div>
			<div class="chart">
				<Line
					data={{
						datasets: [
							{
								label: 'Conditional σ (ann)',
								data: g.conditional_vol.map((r) => ({ x: r.date, y: r.sigma_ann * 100 })),
								borderColor: '#fb923c',
								backgroundColor: 'rgba(251, 146, 60, 0.12)',
								borderWidth: 1.5,
								pointRadius: 0,
								tension: 0.15,
								fill: true
							}
						]
					}}
					options={{
						...chartOptions,
						plugins: { legend: { display: false } },
						scales: {
							...chartOptions.scales,
							y: {
								...chartOptions.scales.y,
								ticks: {
									color: '#94a3b8',
									callback: (v: number | string) => `${Number(v).toFixed(0)}%`
								}
							}
						}
					}}
				/>
			</div>
		</section>
	{/if}

	{#if (summary as { monthly_returns?: Array<{ year: number; months: Array<{ month: number; return_pct: number | null }> }> }).monthly_returns}
		{@const mr = (summary as { monthly_returns: Array<{ year: number; months: Array<{ month: number; return_pct: number | null }> }> }).monthly_returns}
		{#if mr.length > 0}
			<section class="panel">
				<h2>Monthly return heatmap <span class="panel-sub">— compounded daily returns per calendar month</span></h2>
				<table class="heatmap">
					<thead>
						<tr>
							<th></th>
							{#each ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] as m}
								<th>{m}</th>
							{/each}
							<th>Year</th>
						</tr>
					</thead>
					<tbody>
						{#each mr as row}
							{@const year_return = row.months.reduce((acc, c) => acc * (1 + (c.return_pct ?? 0)), 1) - 1}
							<tr>
								<th>{row.year}</th>
								{#each row.months as c}
									<td
										style={c.return_pct == null
											? ''
											: `background: ${c.return_pct >= 0
												? `rgba(74,222,128,${Math.min(1, Math.abs(c.return_pct) * 10)})`
												: `rgba(248,113,113,${Math.min(1, Math.abs(c.return_pct) * 10)})`}`}
										class="cell"
									>
										{c.return_pct == null ? '' : ((c.return_pct * 100) >= 0 ? '+' : '') + (c.return_pct * 100).toFixed(1) + '%'}
									</td>
								{/each}
								<td class="cell year-total" class:up={year_return >= 0} class:down={year_return < 0}>
									{(year_return >= 0 ? '+' : '') + (year_return * 100).toFixed(1)}%
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</section>
		{/if}
	{/if}

	{#if (summary as { rolling_sharpe?: Array<{ date: string; sharpe: number | null }> }).rolling_sharpe}
		{@const rs = (summary as { rolling_sharpe: Array<{ date: string; sharpe: number | null }> }).rolling_sharpe.filter((r) => r.sharpe != null)}
		{#if rs.length > 10}
			<section class="panel">
				<h2>Rolling 60-day Sharpe <span class="panel-sub">— is the alpha stable, or clumped in one stretch?</span></h2>
				<div class="chart">
					<Line
						data={{
							datasets: [
								{
									label: '60d Sharpe',
									data: rs.map((r) => ({ x: r.date, y: r.sharpe as number })),
									borderColor: '#4ade80',
									backgroundColor: 'rgba(74, 222, 128, 0.12)',
									borderWidth: 1.5,
									pointRadius: 0,
									tension: 0.2,
									fill: true
								}
							]
						}}
						options={{
							...chartOptions,
							plugins: { legend: { display: false } },
							scales: {
								...chartOptions.scales,
								y: {
									...chartOptions.scales.y,
									ticks: {
										color: '#94a3b8',
										callback: (v: number | string) => Number(v).toFixed(1)
									}
								}
							}
						}}
					/>
				</div>
			</section>
		{/if}
	{/if}

	{#if (summary as { return_distribution?: { bins: number[]; counts: number[] }; daily_return_stats?: { mean: number; std: number; min: number; max: number; n_days: number } }).return_distribution}
		{@const dist = (summary as { return_distribution: { bins: number[]; counts: number[] } }).return_distribution}
		{@const stats = (summary as { daily_return_stats: { mean: number; std: number; min: number; max: number; n_days: number } }).daily_return_stats}
		<section class="panel">
			<h2>Daily return distribution <span class="panel-sub">— mean {(stats.mean * 100).toFixed(3)}% · σ {(stats.std * 100).toFixed(2)}% · range [{(stats.min * 100).toFixed(2)}%, {(stats.max * 100).toFixed(2)}%] · {stats.n_days} days</span></h2>
			<div class="chart" style="height: 240px">
				<Bar
					data={{
						labels: dist.bins.slice(0, -1).map((b, i) => {
							const lo = b * 100;
							const hi = dist.bins[i + 1] * 100;
							return `${lo.toFixed(1)}..${hi.toFixed(1)}%`;
						}),
						datasets: [
							{
								label: 'Days',
								data: dist.counts,
								backgroundColor: dist.bins
									.slice(0, -1)
									.map((b) =>
										b < 0 ? 'rgba(248, 113, 113, 0.65)' : 'rgba(74, 222, 128, 0.65)'
									)
							}
						]
					}}
					options={{
						responsive: true,
						maintainAspectRatio: false,
						plugins: { legend: { display: false } },
						scales: {
							x: {
								ticks: { color: '#94a3b8', maxRotation: 60, minRotation: 60, autoSkip: true, maxTicksLimit: 10 },
								grid: { display: false }
							},
							y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } }
						}
					}}
				/>
			</div>
		</section>
	{/if}

	{#if (summary as { survivorship?: { n_excluded_signals: number; n_excluded_tickers: number; excluded_tickers: string[]; pct_of_total: number } }).survivorship}
		{@const sv = (summary as { survivorship: { n_excluded_signals: number; n_excluded_tickers: number; excluded_tickers: string[]; pct_of_total: number } }).survivorship}
		{#if sv.n_excluded_signals > 0}
			<section class="panel survivorship-warning">
				<h2>
					Survivorship bias
					<span class="panel-sub">
						— {sv.n_excluded_signals} signals on {sv.n_excluded_tickers} tickers excluded because yfinance has no price data
					</span>
				</h2>
				<div class="baseline-stats">
					<div class="metric">
						<span class="metric-label">Excluded signals</span>
						<span class="metric-value down">{sv.n_excluded_signals}</span>
						<span class="ci">{(sv.pct_of_total * 100).toFixed(1)}% of total</span>
					</div>
					<div class="metric">
						<span class="metric-label">Unique tickers</span>
						<span class="metric-value">{sv.n_excluded_tickers}</span>
						<span class="ci">mostly delisted / acquired names</span>
					</div>
				</div>
				<p class="panel-sub" style="margin:0.5rem 0">
					Delisted-name signals often perform worse (insiders buying into bankruptcy, etc.) — <strong>Sharpe is inflated</strong> by silently dropping them. Set <code>TIINGO_API_KEY</code> in <code>.env</code> to backfill from Tiingo's delisted archive.
				</p>
				{#if sv.excluded_tickers.length}
					<details>
						<summary>Show first {sv.excluded_tickers.length} excluded tickers</summary>
						<p class="excluded-tickers">
							{sv.excluded_tickers.join(' · ')}
						</p>
					</details>
				{/if}
			</section>
		{/if}
	{/if}
	{#if (summary as { skipped_signals?: Record<string, number>; n_signals?: number }).skipped_signals}
		{@const sk = (summary as { skipped_signals: Record<string, number> }).skipped_signals}
		{@const other_skipped = Object.entries(sk)
			.filter(([k, n]) => n > 0 && k !== 'no_prices')
			.map(([k, n]) => `${k.replace(/_/g, ' ')}: ${n}`)}
		{#if other_skipped.length > 0}
			<div class="info">
				Other skipped signals — {other_skipped.join(' · ')}. Insufficient bars means the signal fired too close to the window end to complete a full {(data.params as { hold_days: number }).hold_days}-day hold.
			</div>
		{/if}
	{/if}

	{#if (summary as { exits_by_reason?: Record<string, number> }).exits_by_reason}
		{@const er = (summary as { exits_by_reason: Record<string, number> }).exits_by_reason}
		<section class="panel exit-breakdown">
			<h2>Exits by reason</h2>
			<div class="reason-row">
				{#each Object.entries(er) as [r, n]}
					<span class="reason-chip reason-{r}">{r}: {n}</span>
				{/each}
			</div>
		</section>
	{/if}

	<section class="panel">
		<h2>Trades ({data.trades.length})</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Ticker</th>
						<th>Signal</th>
						<th>Entry</th>
						<th class="num">Entry $</th>
						<th>Exit</th>
						<th class="num">Exit $</th>
						<th>Reason</th>
						<th class="num">Return</th>
						<th class="num">P&L</th>
						<th>Label</th>
					</tr>
				</thead>
				<tbody>
					{#each [...data.trades].sort((a, b) => b.return_pct - a.return_pct) as t}
						<tr>
							<td class="ticker">{t.ticker}</td>
							<td>{t.signal_date}</td>
							<td>{t.entry_date}</td>
							<td class="num">${t.entry_price.toFixed(2)}</td>
							<td>{t.exit_date}</td>
							<td class="num">${t.exit_price.toFixed(2)}</td>
							<td class="reason reason-{t.exit_reason}">{t.exit_reason}</td>
							<td class="num" class:up={t.return_pct >= 0} class:down={t.return_pct < 0}>
								{(t.return_pct >= 0 ? '+' : '') + (t.return_pct * 100).toFixed(2)}%
							</td>
							<td class="num" class:up={t.pnl >= 0} class:down={t.pnl < 0}>
								{fmtMoney(t.pnl)}
							</td>
							<td class="label">{t.label}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{:else}
	<p class="empty">Pick a signal and hit Run.</p>
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	.sweep-link {
		margin-left: 1rem;
		font-size: 0.85rem;
		color: #60a5fa;
		text-decoration: none;
		font-weight: normal;
	}
	.sweep-link:hover {
		text-decoration: underline;
	}
	.subtitle {
		color: #94a3b8;
		margin-top: 0;
		max-width: 720px;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		align-items: end;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1.5rem 0;
	}
	.controls label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.8rem;
		color: #94a3b8;
	}
	.controls input,
	.controls select {
		background: #0f172a;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	.controls button {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.5rem 1.25rem;
		border-radius: 4px;
		font-size: 0.9rem;
		cursor: pointer;
	}
	.controls button:disabled {
		background: #475569;
		cursor: default;
	}
	.controls label.checkbox {
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
	}
	.controls label.checkbox input {
		width: auto;
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	.metric {
		background: #1e293b;
		padding: 0.85rem;
		border-radius: 8px;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.metric-label {
		color: #94a3b8;
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.metric-value {
		font-size: 1.3rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.metric .ci {
		color: #94a3b8;
		font-size: 0.7rem;
		font-variant-numeric: tabular-nums;
	}
	.metric-value.up,
	.up {
		color: #4ade80;
	}
	.metric-value.down,
	.down {
		color: #f87171;
	}
	.panel {
		background: #1e293b;
		padding: 1rem 1.25rem;
		border-radius: 8px;
		margin-bottom: 1rem;
	}
	.panel h2 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.panel-sub {
		color: #64748b;
		font-size: 0.8rem;
		font-weight: normal;
	}
	.chart {
		height: 360px;
	}
	.table-wrap {
		overflow-x: auto;
		max-height: 520px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.45rem 0.6rem;
		text-align: left;
		border-bottom: 1px solid #334155;
	}
	th.num,
	td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	th {
		color: #94a3b8;
		font-weight: 500;
		position: sticky;
		top: 0;
		background: #1e293b;
	}
	.ticker {
		color: #60a5fa;
		font-weight: 600;
	}
	.label {
		color: #94a3b8;
		font-size: 0.8rem;
	}
	.reason {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.reason-stop {
		color: #f87171;
	}
	.reason-trailing {
		color: #fb923c;
	}
	.reason-target {
		color: #4ade80;
	}
	.reason-time {
		color: #94a3b8;
	}
	.exit-breakdown .reason-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.reason-chip {
		background: #0f172a;
		border: 1px solid #334155;
		padding: 0.25rem 0.7rem;
		border-radius: 999px;
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.info {
		background: #1e3a8a;
		color: #bfdbfe;
		padding: 0.6rem 1rem;
		border-radius: 4px;
		margin-bottom: 0.75rem;
		font-size: 0.85rem;
	}
	.warning {
		background: #78350f;
		color: #fed7aa;
		padding: 0.7rem 1rem;
		border-radius: 4px;
		margin-bottom: 0.75rem;
		font-size: 0.9rem;
	}
	.warning a {
		color: #fcd34d;
	}
	.warning code {
		background: #0f172a;
		padding: 0.1rem 0.3rem;
		border-radius: 3px;
		font-size: 0.85em;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
	}
	.empty {
		color: #94a3b8;
	}
	.baseline-header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 0.75rem;
	}
	.baseline-controls {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}
	.baseline-controls label {
		display: flex;
		gap: 0.35rem;
		align-items: center;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	.baseline-controls input {
		width: 4.5rem;
	}
	.baseline-stats {
		display: flex;
		flex-wrap: wrap;
		gap: 1.5rem;
		margin-bottom: 0.5rem;
	}
	.baseline-verdict {
		font-style: italic;
		margin: 0.25rem 0 0.75rem;
	}
	.factor-table {
		width: 100%;
		border-collapse: collapse;
		margin: 0.5rem 0;
		font-size: 0.9rem;
	}
	.factor-table th,
	.factor-table td {
		padding: 0.4rem 0.75rem;
		border-bottom: 1px solid #1f2937;
		text-align: left;
	}
	.factor-table th {
		color: #94a3b8;
		font-weight: 500;
	}
	.factor-table .num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.survivorship-warning {
		border-left: 3px solid #f59e0b;
	}
	.survivorship-warning h2 {
		color: #f59e0b;
	}
	.excluded-tickers {
		font-family: ui-monospace, monospace;
		font-size: 0.82rem;
		color: #94a3b8;
		line-height: 1.6;
		margin: 0.5rem 0 0;
		word-break: break-word;
	}
	.heatmap {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}
	.heatmap th,
	.heatmap td {
		padding: 0.35rem 0.5rem;
		text-align: right;
		border: 1px solid #0f172a;
	}
	.heatmap thead th {
		color: #94a3b8;
		font-weight: 500;
		text-align: center;
		background: #0f172a;
	}
	.heatmap tbody th {
		text-align: left;
		color: #94a3b8;
		background: #0f172a;
	}
	.heatmap .cell {
		color: #e2e8f0;
		font-weight: 500;
	}
	.heatmap .year-total {
		font-weight: 700;
		background: #1e293b;
	}
</style>

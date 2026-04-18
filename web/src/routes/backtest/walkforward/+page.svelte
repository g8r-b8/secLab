<script lang="ts">
	type Metrics = {
		sharpe: number | null;
		cagr: number | null;
		total_return_pct: number | null;
		max_drawdown_pct: number | null;
		alpha_vs_benchmark_pct: number | null;
		n_trades: number | null;
		message?: string | null;
	};

	type ConfigResult = {
		params: {
			hold_days: number;
			stop_loss_pct: number;
			profit_target_pct: number;
			trailing_stop_pct: number;
		};
		train: Metrics;
		test: Metrics;
	};

	type Iteration = {
		train_period: { start: string; end: string; n_signals?: number };
		test_period: { start: string; end: string };
		results: ConfigResult[];
		message?: string;
	};

	type Response = {
		preset: string;
		label: string;
		range: { start: string; end: string };
		n_folds: number;
		rank_by: string;
		top_n: number;
		summary: {
			total_configs_tested: number;
			held_up_out_of_sample: number;
			hold_rate: number;
		};
		iterations: Iteration[];
		error?: string;
	};

	type Preset = { key: string; label: string };

	let presets = $state<Preset[]>([]);
	let preset = $state('cluster_buys');
	let start = $state('2022-01-01');
	let end = $state('2024-12-31');
	let holdDays = $state('10,20,40');
	let stopLoss = $state('0,5,8,10');
	let profitTarget = $state('0,15,25');
	let trailingStop = $state('0');
	let nFolds = $state(2);
	let topN = $state(5);
	let rankBy = $state<'sharpe' | 'alpha_vs_benchmark_pct' | 'cagr'>('sharpe');
	let positionMode = $state<'fixed_usd' | 'pct_portfolio'>('fixed_usd');
	let positionSize = $state(10000);
	let positionPctPct = $state(2);
	let benchmark = $state('SPY');
	let slippageBps = $state(10);
	let commissionPerTrade = $state(1);
	let onePositionPerTicker = $state(false);

	let data = $state<Response | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function loadPresets() {
		const r = await fetch('/api/backtest/presets');
		if (r.ok) presets = (await r.json()).presets ?? [];
	}
	$effect(() => {
		loadPresets();
	});

	function toFracList(s: string): string {
		return s
			.split(',')
			.map((x) => x.trim())
			.filter(Boolean)
			.map((x) => String(Number(x) / 100))
			.join(',');
	}

	async function run() {
		loading = true;
		error = null;
		data = null;
		try {
			const params = new URLSearchParams({
				preset,
				start,
				end,
				hold_days: holdDays,
				stop_loss_pct: toFracList(stopLoss),
				profit_target_pct: toFracList(profitTarget),
				trailing_stop_pct: toFracList(trailingStop),
				n_folds: String(nFolds),
				top_n: String(topN),
				rank_by: rankBy,
				position_mode: positionMode,
				position_size: String(positionSize),
				position_pct: String(positionPctPct / 100),
				benchmark,
				slippage_bps: String(slippageBps),
				commission_per_trade: String(commissionPerTrade),
				one_position_per_ticker: String(onePositionPerTicker)
			});
			const r = await fetch(`/api/backtest/walkforward?${params}`);
			if (!r.ok) {
				const body = await r.json().catch(() => null);
				throw new Error(body?.detail ?? `${r.status} ${r.statusText}`);
			}
			data = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	const fmtPct = (v: number | null | undefined) =>
		v == null ? '—' : `${(v * 100).toFixed(1)}%`;
	const fmtSignedPct = (v: number | null | undefined) =>
		v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
	const fmtNum = (v: number | null | undefined, digits = 2) =>
		v == null ? '—' : v.toFixed(digits);

	function degradationClass(train: number | null, test: number | null): string {
		if (train == null || test == null) return '';
		if (test < 0) return 'collapsed';
		if (test > train * 0.75) return 'held';
		if (test > 0) return 'weakened';
		return 'collapsed';
	}
</script>

<h1>🧪 Walk-Forward Validation</h1>
<p class="subtitle">
	Split a period into N folds. For each adjacent (train, test) pair: sweep the parameter grid on
	train, take the top configs, then re-run them on test. If the Sharpe 2.74 from the sweep is real,
	those configs should beat SPY on the untouched test fold too. If they collapse, the sweep was
	overfitting.
</p>

<section class="controls">
	<label>
		<span>Preset</span>
		<select bind:value={preset}>
			{#each presets as p}
				<option value={p.key}>{p.label}</option>
			{/each}
		</select>
	</label>
	<label><span>Start</span><input type="date" bind:value={start} /></label>
	<label><span>End</span><input type="date" bind:value={end} /></label>
	<label><span>Folds</span><input type="number" min="2" max="8" bind:value={nFolds} /></label>
	<label><span>Top-N per fold</span><input type="number" min="1" max="20" bind:value={topN} /></label>
	<label>
		<span>Rank by</span>
		<select bind:value={rankBy}>
			<option value="sharpe">Sharpe</option>
			<option value="alpha_vs_benchmark_pct">Alpha vs benchmark</option>
			<option value="cagr">CAGR</option>
		</select>
	</label>
	<label><span>Hold days (CSV)</span><input type="text" bind:value={holdDays} /></label>
	<label><span>Stop % (CSV)</span><input type="text" bind:value={stopLoss} /></label>
	<label><span>Target % (CSV)</span><input type="text" bind:value={profitTarget} /></label>
	<label><span>Trail % (CSV)</span><input type="text" bind:value={trailingStop} /></label>
	<label>
		<span>Sizing</span>
		<select bind:value={positionMode}>
			<option value="fixed_usd">Fixed $</option>
			<option value="pct_portfolio">% of portfolio</option>
		</select>
	</label>
	{#if positionMode === 'fixed_usd'}
		<label><span>Position $</span><input type="number" bind:value={positionSize} /></label>
	{:else}
		<label><span>Position % of equity</span><input type="number" min="0.1" max="100" step="0.5" bind:value={positionPctPct} /></label>
	{/if}
	<label><span>Slippage (bps)</span><input type="number" min="0" step="1" bind:value={slippageBps} /></label>
	<label><span>Commission $/trade</span><input type="number" min="0" step="0.5" bind:value={commissionPerTrade} /></label>
	<label class="chkbox"><input type="checkbox" bind:checked={onePositionPerTicker} /> <span>1 position / ticker</span></label>
	<label><span>Benchmark</span><input type="text" bind:value={benchmark} size="5" /></label>
	<button onclick={run} disabled={loading}>{loading ? 'Running…' : 'Run walk-forward'}</button>
</section>

<section class="quick-links">
	<a href="/backtest">single backtest</a> ·
	<a href="/backtest/sweep">param sweep</a>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}

{#if loading}
	<div class="status">
		Running walk-forward… <strong>{nFolds}</strong> folds × ~{holdDays.split(',').length * stopLoss.split(',').length * profitTarget.split(',').length}
		combos per sweep on train + top-{topN} backtests on test. Expect several minutes.
	</div>
{:else if data && !data.error}
	<section class="meta">
		<span>{data.label}</span>
		<span>· {data.range.start} → {data.range.end}</span>
		<span>· {data.n_folds} folds</span>
		<span>· ranked by <strong>{data.rank_by}</strong></span>
	</section>

	<section class="headline-kpis">
		<div class="kpi">
			<span class="kpi-label">Configs tested</span>
			<span class="kpi-value">{data.summary.total_configs_tested}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Held up out-of-sample</span>
			<span class="kpi-value up">{data.summary.held_up_out_of_sample}</span>
		</div>
		<div
			class="kpi"
			class:up={data.summary.hold_rate >= 0.5}
			class:down={data.summary.hold_rate < 0.5}
		>
			<span class="kpi-label">Hold rate</span>
			<span class="kpi-value">{fmtPct(data.summary.hold_rate)}</span>
		</div>
	</section>

	{#each data.iterations as iter, idx}
		<section class="panel">
			<h2>
				Iteration {idx + 1}
				<span class="panel-sub">
					train {iter.train_period.start} → {iter.train_period.end}
					{#if iter.train_period.n_signals}· {iter.train_period.n_signals.toLocaleString()} signals{/if}
					· test {iter.test_period.start} → {iter.test_period.end}
				</span>
			</h2>

			{#if iter.message}
				<p class="empty">{iter.message}</p>
			{:else if iter.results.length === 0}
				<p class="empty">No results.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th rowspan="2">Params</th>
								<th colspan="5" class="group train">IN-SAMPLE (train)</th>
								<th colspan="5" class="group test">OUT-OF-SAMPLE (test)</th>
							</tr>
							<tr>
								<th class="num">Trades</th>
								<th class="num">Sharpe</th>
								<th class="num">CAGR</th>
								<th class="num">α</th>
								<th class="num">MaxDD</th>
								<th class="num">Trades</th>
								<th class="num">Sharpe</th>
								<th class="num">CAGR</th>
								<th class="num">α</th>
								<th class="num">MaxDD</th>
							</tr>
						</thead>
						<tbody>
							{#each iter.results as r}
								{@const shClass = degradationClass(r.train.sharpe, r.test.sharpe)}
								{@const alphaClass = degradationClass(r.train.alpha_vs_benchmark_pct, r.test.alpha_vs_benchmark_pct)}
								<tr>
									<td class="params">
										hold={r.params.hold_days} · stop={(r.params.stop_loss_pct * 100).toFixed(0)}% · tgt={(r.params.profit_target_pct * 100).toFixed(0)}%
									</td>
									<td class="num train">{r.train.n_trades ?? '—'}</td>
									<td class="num train">{fmtNum(r.train.sharpe)}</td>
									<td class="num train">{fmtPct(r.train.cagr)}</td>
									<td class="num train">{fmtSignedPct(r.train.alpha_vs_benchmark_pct)}</td>
									<td class="num train">{fmtPct(r.train.max_drawdown_pct)}</td>
									<td class="num test">{r.test.n_trades ?? '—'}</td>
									<td class="num test state-{shClass}">{fmtNum(r.test.sharpe)}</td>
									<td class="num test">{fmtPct(r.test.cagr)}</td>
									<td class="num test state-{alphaClass}">{fmtSignedPct(r.test.alpha_vs_benchmark_pct)}</td>
									<td class="num test">{fmtPct(r.test.max_drawdown_pct)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/each}
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	.subtitle {
		color: #94a3b8;
		margin-top: 0;
		max-width: 780px;
	}
	.controls {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1.5rem 0 0.75rem;
	}
	.controls label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.75rem;
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
		padding: 0.55rem 1rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.9rem;
		align-self: end;
	}
	.controls label.chkbox { flex-direction: row; align-items: center; gap: 0.4rem; }
	.controls label.chkbox input { width: auto; }
	.quick-links {
		margin-bottom: 1rem;
		font-size: 0.85rem;
	}
	.quick-links a {
		color: #60a5fa;
		text-decoration: none;
		margin-right: 0.5rem;
	}
	.meta {
		display: flex;
		gap: 0.5rem;
		color: #94a3b8;
		font-size: 0.9rem;
		flex-wrap: wrap;
		margin-bottom: 1rem;
	}
	.headline-kpis {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	.kpi {
		background: #1e293b;
		padding: 0.9rem 1rem;
		border-radius: 8px;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.kpi-label {
		color: #94a3b8;
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.kpi-value {
		font-size: 1.6rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.kpi.up { border: 1px solid #166534; background: #14532d40; }
	.kpi.down { border: 1px solid #7f1d1d; background: #7f1d1d40; }
	.up { color: #4ade80; }
	.down { color: #f87171; }
	.panel {
		background: #1e293b;
		padding: 1rem;
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
	.table-wrap { overflow: auto; }
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	th, td {
		padding: 0.35rem 0.5rem;
		text-align: left;
		border-bottom: 1px solid #334155;
	}
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	th { color: #94a3b8; font-weight: 500; position: sticky; top: 0; background: #1e293b; }
	th.group {
		text-align: center;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		font-size: 0.7rem;
	}
	th.group.train { border-bottom: 2px solid #334155; color: #94a3b8; }
	th.group.test { border-bottom: 2px solid #60a5fa; color: #60a5fa; }
	.params { color: #e2e8f0; font-variant-numeric: tabular-nums; white-space: nowrap; }
	td.train { color: #94a3b8; }
	td.test { color: #e2e8f0; }
	.state-held { color: #4ade80; font-weight: 600; background: #14532d40; }
	.state-weakened { color: #facc15; background: #78350f30; }
	.state-collapsed { color: #f87171; background: #7f1d1d40; }
	.status {
		color: #94a3b8;
		font-style: italic;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
	}
	.empty { color: #94a3b8; }
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
	}
</style>

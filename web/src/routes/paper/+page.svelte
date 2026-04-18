<script lang="ts">
	import { untrack } from 'svelte';
	import { Line } from 'svelte-chartjs';
	import {
		Chart as ChartJS,
		LineElement,
		LineController,
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
		PointElement,
		CategoryScale,
		LinearScale,
		TimeScale,
		Tooltip,
		Legend,
		Filler
	);

	type PortfolioRow = {
		id: number;
		name: string;
		preset: string;
		started_at: string;
		starting_cash: number;
		status: string;
		last_updated: string | null;
		current_equity: number;
		total_return_pct: number;
		n_open_positions: number;
		n_trades: number;
	};

	type Trade = {
		ticker: string;
		signal_date: string;
		entry_date: string;
		entry_price: number;
		shares: number;
		position_value: number;
		exit_date: string | null;
		exit_price: number | null;
		exit_reason: string | null;
		pnl: number | null;
		return_pct: number | null;
		label: string;
		strength: number;
	};

	type EquityRow = { date: string; equity: number; cash: number; invested: number };

	type PortfolioDetail = {
		id: number;
		name: string;
		preset: string;
		config: Record<string, unknown>;
		started_at: string;
		starting_cash: number;
		status: string;
		last_updated: string | null;
		equity_curve: EquityRow[];
		trades: Trade[];
	};

	type Preset = { key: string; label: string };

	let portfolios = $state<PortfolioRow[]>([]);
	let selected = $state<PortfolioDetail | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let presets = $state<Preset[]>([]);

	// New-portfolio form state, seeded with champion config
	let newName = $state('');
	let newPreset = $state('cluster_buys');
	let newStartedAt = $state(new Date().toISOString().slice(0, 10));
	let newStartingCash = $state(100000);
	let newHoldDays = $state(5);
	let newPositionMode = $state<
		'fixed_usd' | 'pct_portfolio' | 'strength_weighted' | 'target_deployment' | 'risk_per_trade' | 'vol_target'
	>('strength_weighted');
	let newPositionPctPct = $state(2);
	let newPositionSize = $state(10000);
	let newStrengthCap = $state(3);
	let newStopLossPct = $state(3);
	let newProfitTargetPct = $state(10);
	let newSlippageBps = $state(10);
	let newCommission = $state(1);
	let creating = $state(false);

	async function loadPortfolios() {
		const r = await fetch('/api/paper');
		if (r.ok) portfolios = (await r.json()).portfolios ?? [];
	}
	async function loadPresets() {
		const r = await fetch('/api/backtest/presets');
		if (r.ok) presets = (await r.json()).presets ?? [];
	}
	async function loadDetail(id: number) {
		loading = true;
		error = null;
		try {
			const r = await fetch(`/api/paper/${id}`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			selected = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			selected = null;
		} finally {
			loading = false;
		}
	}
	async function advance(id: number) {
		loading = true;
		error = null;
		try {
			const r = await fetch(`/api/paper/${id}/advance`, { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			selected = await r.json();
			await loadPortfolios();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
	async function advanceAll() {
		loading = true;
		try {
			await fetch('/api/paper/advance-all', { method: 'POST' });
			await loadPortfolios();
			if (selected) await loadDetail(selected.id);
		} finally {
			loading = false;
		}
	}
	async function createPortfolio() {
		if (!newName.trim()) {
			error = 'Name required.';
			return;
		}
		creating = true;
		error = null;
		try {
			const config: Record<string, unknown> = {
				hold_days: newHoldDays,
				position_mode: newPositionMode,
				position_pct: newPositionPctPct / 100,
				position_size: newPositionSize,
				strength_scale_cap: newStrengthCap,
				stop_loss_pct: newStopLossPct / 100,
				profit_target_pct: newProfitTargetPct / 100,
				slippage_bps: newSlippageBps,
				commission_per_trade: newCommission
			};
			const r = await fetch('/api/paper/create', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					name: newName,
					preset: newPreset,
					starting_cash: newStartingCash,
					started_at: newStartedAt,
					config
				})
			});
			if (!r.ok) {
				const b = await r.json().catch(() => ({}));
				throw new Error(b?.detail ?? `${r.status} ${r.statusText}`);
			}
			newName = '';
			await loadPortfolios();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			creating = false;
		}
	}
	async function deletePortfolio(id: number) {
		if (!confirm('Delete this portfolio permanently?')) return;
		await fetch(`/api/paper/${id}`, { method: 'DELETE' });
		if (selected?.id === id) selected = null;
		await loadPortfolios();
	}

	$effect(() => {
		untrack(() => {
			loadPortfolios();
			loadPresets();
		});
	});

	const equityChart = $derived.by(() => {
		if (!selected?.equity_curve?.length) return null;
		const base = selected.equity_curve[0].equity || 1;
		return {
			datasets: [
				{
					label: 'Equity',
					data: selected.equity_curve.map((r) => ({
						x: r.date,
						y: (r.equity / base - 1) * 100
					})),
					borderColor: '#60a5fa',
					backgroundColor: 'rgba(96, 165, 250, 0.12)',
					borderWidth: 2,
					pointRadius: 0,
					tension: 0.1,
					fill: true
				}
			]
		};
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: { legend: { display: false } },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'month' as const },
				ticks: { color: '#94a3b8', maxRotation: 0 },
				grid: { color: '#1e293b' }
			},
			y: {
				ticks: {
					color: '#94a3b8',
					callback: (v: number | string) => `${Number(v).toFixed(0)}%`
				},
				grid: { color: '#1e293b' }
			}
		}
	};

	function fmtPct(x: number | null | undefined, digits = 2): string {
		if (x == null) return '—';
		const sign = x >= 0 ? '+' : '';
		return sign + (x * 100).toFixed(digits) + '%';
	}
	function fmtUsd(x: number | null | undefined): string {
		if (x == null) return '—';
		if (Math.abs(x) >= 1_000_000) return `$${(x / 1_000_000).toFixed(2)}M`;
		if (Math.abs(x) >= 1_000) return `$${(x / 1_000).toFixed(1)}k`;
		return `$${x.toFixed(0)}`;
	}
</script>

<svelte:head><title>Paper portfolios · secLab</title></svelte:head>

<h1>Paper trading <span class="panel-sub">— forward-test the champion config on fresh data</span></h1>

{#if error}
	<div class="error">{error}</div>
{/if}

<section class="panel">
	<div class="panel-head">
		<h2>Portfolios ({portfolios.length})</h2>
		<button onclick={advanceAll} disabled={loading || !portfolios.length}>
			{loading ? '…' : 'Advance all → today'}
		</button>
	</div>
	{#if portfolios.length === 0}
		<p class="empty">No portfolios yet. Create one below.</p>
	{:else}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Name</th>
						<th>Preset</th>
						<th class="num">Start</th>
						<th class="num">Last updated</th>
						<th class="num">Starting $</th>
						<th class="num">Current $</th>
						<th class="num">Return</th>
						<th class="num">Trades</th>
						<th class="num">Open</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each portfolios as p}
						<tr
							class:selected={selected?.id === p.id}
							onclick={() => loadDetail(p.id)}
						>
							<td class="name">{p.name}</td>
							<td>{p.preset}</td>
							<td class="num">{p.started_at ?? '—'}</td>
							<td class="num">{p.last_updated ?? '—'}</td>
							<td class="num">{fmtUsd(p.starting_cash)}</td>
							<td class="num">{fmtUsd(p.current_equity)}</td>
							<td
								class="num"
								class:up={p.total_return_pct >= 0}
								class:down={p.total_return_pct < 0}
							>
								{fmtPct(p.total_return_pct)}
							</td>
							<td class="num">{p.n_trades}</td>
							<td class="num">{p.n_open_positions}</td>
							<td>
								<button
									onclick={(e) => {
										e.stopPropagation();
										advance(p.id);
									}}
									title="Roll this portfolio forward to today"
									disabled={loading}
								>
									↻
								</button>
								<button
									onclick={(e) => {
										e.stopPropagation();
										deletePortfolio(p.id);
									}}
									class="danger"
									title="Delete"
								>
									×
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<section class="panel">
	<h2>Create new portfolio</h2>
	<div class="form-grid">
		<label>
			<span>Name</span>
			<input type="text" bind:value={newName} placeholder="e.g. champion-live" />
		</label>
		<label>
			<span>Preset</span>
			<select bind:value={newPreset}>
				{#each presets as p}
					<option value={p.key}>{p.label}</option>
				{/each}
			</select>
		</label>
		<label>
			<span>Start date</span>
			<input type="date" bind:value={newStartedAt} />
		</label>
		<label>
			<span>Starting $</span>
			<input type="number" min="1000" step="1000" bind:value={newStartingCash} />
		</label>
		<label>
			<span>Hold days</span>
			<input type="number" min="1" max="252" bind:value={newHoldDays} />
		</label>
		<label>
			<span>Sizing mode</span>
			<select bind:value={newPositionMode}>
				<option value="fixed_usd">Fixed $</option>
				<option value="pct_portfolio">% of portfolio</option>
				<option value="risk_per_trade">Risk / trade</option>
				<option value="vol_target">Vol target</option>
				<option value="strength_weighted">Strength weighted</option>
				<option value="target_deployment">Target deployment</option>
			</select>
		</label>
		{#if newPositionMode === 'fixed_usd'}
			<label>
				<span>Position $</span>
				<input type="number" min="100" step="1000" bind:value={newPositionSize} />
			</label>
		{:else}
			<label>
				<span>Position % of equity</span>
				<input type="number" min="0.1" max="100" step="0.5" bind:value={newPositionPctPct} />
			</label>
		{/if}
		{#if newPositionMode === 'strength_weighted'}
			<label>
				<span>Strength cap ×</span>
				<input type="number" min="1" max="10" step="0.5" bind:value={newStrengthCap} />
			</label>
		{/if}
		<label>
			<span>Stop-loss %</span>
			<input type="number" min="0" max="50" step="1" bind:value={newStopLossPct} />
		</label>
		<label>
			<span>Profit target %</span>
			<input type="number" min="0" max="500" step="1" bind:value={newProfitTargetPct} />
		</label>
		<label>
			<span>Slippage bps</span>
			<input type="number" min="0" max="500" step="1" bind:value={newSlippageBps} />
		</label>
		<label>
			<span>Commission $</span>
			<input type="number" min="0" max="100" step="0.5" bind:value={newCommission} />
		</label>
	</div>
	<button onclick={createPortfolio} disabled={creating || !newName.trim()} class="primary">
		{creating ? 'Creating…' : 'Create portfolio'}
	</button>
</section>

{#if selected}
	<section class="panel">
		<div class="panel-head">
			<h2>
				{selected.name}
				<span class="panel-sub">
					{selected.preset} · started {selected.started_at} · last updated {selected.last_updated ?? 'never'}
				</span>
			</h2>
			<button onclick={() => advance(selected!.id)} disabled={loading}>
				Advance → today
			</button>
		</div>
		{#if equityChart}
			<div class="chart">
				<Line data={equityChart} options={chartOptions} />
			</div>
		{/if}
		<details>
			<summary>Config ({Object.keys(selected.config).length} params)</summary>
			<pre class="config">{JSON.stringify(selected.config, null, 2)}</pre>
		</details>
	</section>

	{#if selected.trades.length > 0}
		<section class="panel">
			<h2>Recent trades ({selected.trades.length} shown)</h2>
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
						</tr>
					</thead>
					<tbody>
						{#each selected.trades as t}
							<tr>
								<td class="ticker">{t.ticker}</td>
								<td>{t.signal_date}</td>
								<td>{t.entry_date}</td>
								<td class="num">${t.entry_price?.toFixed(2) ?? '—'}</td>
								<td>{t.exit_date ?? '(open)'}</td>
								<td class="num">{t.exit_price != null ? '$' + t.exit_price.toFixed(2) : '—'}</td>
								<td>{t.exit_reason ?? '—'}</td>
								<td
									class="num"
									class:up={(t.return_pct ?? 0) >= 0}
									class:down={(t.return_pct ?? 0) < 0}
								>
									{t.return_pct != null ? fmtPct(t.return_pct) : '—'}
								</td>
								<td
									class="num"
									class:up={(t.pnl ?? 0) >= 0}
									class:down={(t.pnl ?? 0) < 0}
								>
									{t.pnl != null ? fmtUsd(t.pnl) : '—'}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
{/if}

<style>
	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.75rem;
	}
	h2 {
		margin: 0 0 0.75rem;
		font-size: 1.1rem;
	}
	.panel-sub {
		font-weight: normal;
		font-size: 0.85rem;
		color: #94a3b8;
		margin-left: 0.35rem;
	}
	.panel {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
	}
	.panel-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 0.5rem;
	}
	.form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 0.75rem 1rem;
		margin-bottom: 1rem;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	input,
	select {
		background: #1e293b;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	button {
		background: #334155;
		color: #e2e8f0;
		border: none;
		padding: 0.4rem 0.85rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.85rem;
	}
	button:hover:not(:disabled) {
		background: #475569;
	}
	button.primary {
		background: #2563eb;
	}
	button.primary:hover:not(:disabled) {
		background: #3b82f6;
	}
	button.danger {
		background: #7f1d1d;
	}
	button:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.88rem;
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 0.4rem 0.6rem;
		text-align: left;
		border-bottom: 1px solid #1e293b;
	}
	th {
		color: #94a3b8;
		font-weight: 500;
	}
	td.num,
	th.num {
		text-align: right;
	}
	td.name {
		font-weight: 600;
	}
	td.ticker {
		font-family: ui-monospace, monospace;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	tr.selected {
		background: #1e293b;
	}
	tbody tr:hover {
		cursor: pointer;
		background: #1e293b;
	}
	.chart {
		height: 320px;
		margin-bottom: 0.5rem;
	}
	.config {
		background: #020617;
		padding: 0.75rem;
		border-radius: 4px;
		font-size: 0.8rem;
		overflow-x: auto;
		margin: 0.5rem 0 0;
	}
	.empty {
		color: #94a3b8;
		font-style: italic;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
		margin-bottom: 1rem;
	}
</style>

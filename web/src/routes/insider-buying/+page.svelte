<script lang="ts">
	import { Bar } from 'svelte-chartjs';
	import {
		Chart as ChartJS,
		CategoryScale,
		LinearScale,
		BarElement,
		Title,
		Tooltip,
		Legend
	} from 'chart.js';
	import PriceImpactChart from '$lib/PriceImpactChart.svelte';
	import TickerTimeline from '$lib/TickerTimeline.svelte';

	ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

	let selectedTicker = $state<string | null>(null);
	let signal = $state<'buys' | 'sells'>('buys');

	type LeaderboardRow = {
		ticker: string;
		issuer: string;
		total_$: string;
		shares: string;
		'# insiders'?: number;
		'# sellers'?: number;
		'C-suite': string;
		positions: string;
		value_num: number;
	};

	type Summary = {
		filings_scanned?: number;
		purchase_transactions?: number;
		planned_sale_filings?: number;
		total_value: number;
		cluster_tickers: number;
		csuite_tickers: number;
	};

	type SignalsResponse = {
		range: { start: string; end: string };
		coverage?: {
			// Form 4 ingest coverage
			covered?: string[];
			missing_current?: string | null;
			newly_ingested?: string[] | string[];
			// Form 144 per-day coverage
			missing_days?: number;
			ingested_days?: string[];
			pending_days?: number;
		};
		warnings?: string[];
		providers?: { finnhub: boolean };
		summary: Summary;
		leaderboard: LeaderboardRow[];
		transactions: Array<Record<string, unknown>>;
	};

	const today = new Date();
	// Default window: start of last calendar year → today. Uses the Form 4 DERA
	// backfill (2021Q1 onward) so the page has real data on first load.
	const defaultStart = `${today.getFullYear() - 1}-01-01`;
	const todayIso = today.toISOString().slice(0, 10);

	let start = $state(defaultStart);
	let end = $state(todayIso);
	let topN = $state(25);
	let minValueK = $state(0);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let data = $state<SignalsResponse | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				start,
				end,
				top: String(topN),
				min_value: String(minValueK * 1000)
			});
			const endpoint =
				signal === 'buys' ? '/api/signals/insider-buying' : '/api/signals/planned-sells';
			const res = await fetch(`${endpoint}?${params}`);
			if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
			data = await res.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			data = null;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		signal;
		load();
	});

	const peopleCount = (r: LeaderboardRow): number =>
		(r['# insiders'] ?? r['# sellers'] ?? 0) as number;

	const chartRows = $derived.by(() => {
		if (!data?.leaderboard?.length) return [] as LeaderboardRow[];
		return [...data.leaderboard].sort((a, b) => b.value_num - a.value_num).slice(0, 15).reverse();
	});

	const chartData = $derived.by(() => {
		if (!chartRows.length) return null;
		const sellColor = signal === 'sells';
		return {
			labels: chartRows.map((r) => {
				const star = r['C-suite'] === 'Y' ? ' *' : '';
				const cluster = peopleCount(r) >= 2 ? ' ●●' : '';
				return `${r.ticker}${star}${cluster}`;
			}),
			datasets: [
				{
					label: sellColor ? 'Total $ planned to sell' : 'Total $ bought',
					data: chartRows.map((r) => r.value_num),
					backgroundColor: chartRows.map((r) => {
						const n = Math.min(peopleCount(r), 5);
						const opacity = 0.4 + n * 0.12;
						return sellColor
							? `rgba(248, 113, 113, ${opacity})`
							: `rgba(96, 165, 250, ${opacity})`;
					})
				}
			]
		};
	});

	// Chart.js type generics make this painful — using `any` for options here.
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const chartOptions = $derived<any>({
		indexAxis: 'y' as const,
		responsive: true,
		maintainAspectRatio: false,
		onClick: (_e: unknown, els: Array<{ index: number }>) => {
			if (els.length && chartRows[els[0].index]) {
				selectedTicker = chartRows[els[0].index].ticker;
			}
		},
		onHover: (event: { native?: Event | null }, els: unknown[]) => {
			const target = event.native?.target as HTMLElement | undefined;
			if (target) target.style.cursor = els.length ? 'pointer' : 'default';
		},
		plugins: { legend: { display: false } },
		scales: {
			x: {
				ticks: {
					color: '#94a3b8',
					callback: (v: number | string) => '$' + Number(v).toLocaleString()
				},
				grid: { color: '#1e293b' }
			},
			y: { ticks: { color: '#e2e8f0' }, grid: { color: '#1e293b' } }
		}
	});

	const fmtMoney = (n: number) => {
		if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
		if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
		return `$${n.toFixed(0)}`;
	};
</script>

<h1>{signal === 'buys' ? 'Insider Buying' : 'Planned Insider Sales'}</h1>
<p class="subtitle">
	{signal === 'buys'
		? 'SEC Form 4 open-market purchases (code P), ranked by ticker.'
		: 'SEC Form 144 notices of proposed sale. Filed ~30 days before execution — forward-looking sell signal.'}
</p>

<div class="tabs">
	<button class:active={signal === 'buys'} onclick={() => (signal = 'buys')}>
		Buys (Form 4)
	</button>
	<button class:active={signal === 'sells'} onclick={() => (signal = 'sells')}>
		Planned Sells (Form 144)
	</button>
</div>

<section class="controls">
	<label>
		<span>Start</span>
		<input type="date" bind:value={start} />
	</label>
	<label>
		<span>End</span>
		<input type="date" bind:value={end} />
	</label>
	<label>
		<span>Min $ (000s)</span>
		<input type="number" min="0" step="10" bind:value={minValueK} />
	</label>
	<label>
		<span>Top N</span>
		<input type="number" min="5" max="200" step="5" bind:value={topN} />
	</label>
	<button onclick={load} disabled={loading}>
		{loading ? 'Loading…' : 'Run'}
	</button>
</section>

{#if error}
	<div class="error">Error: {error}</div>
{/if}

{#if data?.warnings?.length}
	{#each data.warnings as w}
		<div class="warning">{w}</div>
	{/each}
{/if}

{#if data?.coverage?.newly_ingested?.length}
	<div class="info">
		Ingested new quarter{data.coverage.newly_ingested.length > 1 ? 's' : ''}:
		{data.coverage.newly_ingested.join(', ')}
	</div>
{/if}

{#if loading}
	<div class="status">Fetching Form 4 filings {start} → {end}…</div>
{:else if data}
	<section class="kpis">
		{#if signal === 'buys'}
			<div class="kpi">
				<span class="kpi-label">Filings scanned</span>
				<span class="kpi-value">{(data.summary.filings_scanned ?? 0).toLocaleString()}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">Purchase txns</span>
				<span class="kpi-value">{(data.summary.purchase_transactions ?? 0).toLocaleString()}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">Total $ bought</span>
				<span class="kpi-value">{fmtMoney(data.summary.total_value)}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">Cluster buys (≥2)</span>
				<span class="kpi-value">{data.summary.cluster_tickers}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">C-suite buying</span>
				<span class="kpi-value">{data.summary.csuite_tickers}</span>
			</div>
		{:else}
			<div class="kpi">
				<span class="kpi-label">Planned sale filings</span>
				<span class="kpi-value">{(data.summary.planned_sale_filings ?? 0).toLocaleString()}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">Total $ planned to sell</span>
				<span class="kpi-value">{fmtMoney(data.summary.total_value)}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">Cluster sells (≥2)</span>
				<span class="kpi-value">{data.summary.cluster_tickers}</span>
			</div>
			<div class="kpi">
				<span class="kpi-label">C-suite selling</span>
				<span class="kpi-value">{data.summary.csuite_tickers}</span>
			</div>
		{/if}
	</section>

	<div class="grid">
		<section class="panel">
			<h2>Top tickers by $ {signal === 'buys' ? 'purchased' : 'planned to sell'}</h2>
			{#if chartData}
				<div class="chart">
					<Bar data={chartData} options={chartOptions} />
				</div>
			{:else}
				<p class="empty">No purchases in this window.</p>
			{/if}
		</section>

		<section class="panel">
			<h2>Leaderboard</h2>
			<p class="hint">Click any ticker (chart or table) to see price impact below.</p>
			{#if data.leaderboard.length}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Ticker</th>
								<th>Total $</th>
								<th>{signal === 'buys' ? '# Insiders' : '# Sellers'}</th>
								<th>C-Suite</th>
								<th>Positions</th>
							</tr>
						</thead>
						<tbody>
							{#each data.leaderboard as row}
								<tr
									class="clickable"
									class:selected={row.ticker === selectedTicker}
									onclick={() => (selectedTicker = row.ticker)}
								>
									<td class="ticker">{row.ticker}</td>
									<td class="num">{row.total_$}</td>
									<td class="num">{peopleCount(row)}</td>
									<td>{row['C-suite']}</td>
									<td class="positions">{row.positions}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="empty">No purchases in this window.</p>
			{/if}
		</section>
	</div>

{/if}

<svelte:window onkeydown={(e) => selectedTicker && e.key === 'Escape' && (selectedTicker = null)} />

{#if selectedTicker}
	<button
		class="overlay-backdrop"
		onclick={() => (selectedTicker = null)}
		aria-label="Close detail view"
	></button>
	<div class="modal" role="dialog" aria-modal="true" aria-label="{selectedTicker} insider impact">
		<header class="modal-header">
			<h2>{selectedTicker} <span class="modal-sub">— insider impact · {start} → {end}</span></h2>
			<button class="close" onclick={() => (selectedTicker = null)} aria-label="Close">×</button>
		</header>
		<div class="modal-body">
			<PriceImpactChart ticker={selectedTicker} {start} {end} />
			<TickerTimeline ticker={selectedTicker} {start} {end} />
		</div>
	</div>
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	.subtitle {
		color: #94a3b8;
		margin-top: 0;
	}
	.tabs {
		display: flex;
		gap: 0.25rem;
		margin: 1rem 0 0;
		border-bottom: 1px solid #334155;
	}
	.tabs button {
		background: transparent;
		border: none;
		color: #94a3b8;
		padding: 0.6rem 1rem;
		cursor: pointer;
		font-size: 0.9rem;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}
	.tabs button:hover {
		color: #e2e8f0;
	}
	.tabs button.active {
		color: #60a5fa;
		border-bottom-color: #60a5fa;
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
	.controls input {
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
	.kpis {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		margin-bottom: 1.5rem;
	}
	.kpi {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.kpi-label {
		color: #94a3b8;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.kpi-value {
		font-size: 1.5rem;
		font-weight: 600;
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
	}
	@media (max-width: 900px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
	.panel {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
	}
	.panel h2 {
		margin: 0 0 1rem;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.chart {
		height: 420px;
	}
	.table-wrap {
		overflow: auto;
		max-height: 600px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.5rem;
		text-align: left;
		border-bottom: 1px solid #334155;
	}
	th {
		color: #94a3b8;
		font-weight: 500;
		position: sticky;
		top: 0;
		background: #1e293b;
	}
	.ticker {
		font-weight: 600;
		color: #60a5fa;
	}
	.num {
		font-variant-numeric: tabular-nums;
	}
	.positions {
		color: #94a3b8;
		font-size: 0.8rem;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
	}
	.empty {
		color: #94a3b8;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
	}
	.warning {
		background: #78350f;
		color: #fed7aa;
		padding: 0.6rem 1rem;
		border-radius: 4px;
		margin-bottom: 0.5rem;
		font-size: 0.9rem;
	}
	.info {
		background: #1e3a8a;
		color: #bfdbfe;
		padding: 0.6rem 1rem;
		border-radius: 4px;
		margin-bottom: 0.5rem;
		font-size: 0.9rem;
	}
	.hint {
		color: #64748b;
		font-size: 0.8rem;
		margin: 0 0 0.75rem;
	}
	tr.clickable {
		cursor: pointer;
		transition: background 100ms;
	}
	tr.clickable:hover {
		background: #0f172a;
	}
	tr.clickable.selected {
		background: #1e3a8a40;
	}
	.overlay-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.72);
		border: none;
		cursor: pointer;
		z-index: 100;
		padding: 0;
	}
	.modal {
		position: fixed;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		background: #0f172a;
		border: 1px solid #334155;
		border-radius: 12px;
		width: calc(100vw - 4rem);
		max-width: 1100px;
		max-height: calc(100vh - 4rem);
		overflow-y: auto;
		box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
		z-index: 101;
	}
	.modal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 1rem 1.25rem;
		border-bottom: 1px solid #1e293b;
		position: sticky;
		top: 0;
		background: #0f172a;
		z-index: 1;
	}
	.modal-header h2 {
		margin: 0;
		font-size: 1.25rem;
		color: #60a5fa;
	}
	.modal-sub {
		color: #94a3b8;
		font-size: 0.85rem;
		font-weight: normal;
	}
	.close {
		background: transparent;
		border: 1px solid #334155;
		color: #cbd5e1;
		padding: 0.3rem 0.7rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.9rem;
	}
	.close:hover {
		background: #1e293b;
	}
	.modal-body {
		padding: 1rem 1.25rem 1.5rem;
	}
</style>

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

	type Row = {
		ticker: string;
		dp_shares: number;
		dp_trades: number;
		dp_notional: number;
		na_shares: number;
		na_trades: number;
		na_notional: number;
		ats_share: number;
		na_share: number;
		insider_net_usd: number;
		n_buys: number;
		n_sells: number;
	};

	type Latest = {
		week_start: string | null;
		smart: Row[];
		dumb: Row[];
	};

	type TickerWeek = {
		week_start: string;
		dp_shares: number;
		dp_notional: number;
		na_shares: number;
		na_notional: number;
		ats_share: number | null;
		insider_net_usd: number;
		n_buys: number;
		n_sells: number;
	};

	let latest = $state<Latest | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	let tickerInput = $state('');
	let selectedTicker = $state<string | null>(null);
	let series = $state<TickerWeek[]>([]);
	let detailLoading = $state(false);

	async function loadLatest() {
		loading = true;
		error = null;
		try {
			const r = await fetch('/api/signals/smart-vs-dumb?top=30');
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			latest = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function loadDetail(ticker: string) {
		selectedTicker = ticker;
		detailLoading = true;
		series = [];
		try {
			const r = await fetch(`/api/signals/smart-vs-dumb/${encodeURIComponent(ticker)}?weeks=26`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			series = body.series ?? [];
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			detailLoading = false;
		}
	}

	$effect(() => {
		untrack(() => loadLatest());
	});

	const tickerChart = $derived.by(() => {
		if (!series.length) return null;
		const ordered = [...series].reverse(); // oldest → newest for x-axis
		return {
			datasets: [
				{
					label: 'Dark pool $ (ATS)',
					data: ordered.map((r) => ({ x: r.week_start, y: r.dp_notional / 1e6 })),
					borderColor: '#60a5fa',
					backgroundColor: 'rgba(96, 165, 250, 0.12)',
					borderWidth: 2,
					pointRadius: 0,
					tension: 0.15,
					fill: true,
					yAxisID: 'y'
				},
				{
					label: 'Wholesaler $ (non-ATS)',
					data: ordered.map((r) => ({ x: r.week_start, y: r.na_notional / 1e6 })),
					borderColor: '#f59e0b',
					backgroundColor: 'rgba(245, 158, 11, 0.10)',
					borderWidth: 2,
					pointRadius: 0,
					tension: 0.15,
					fill: true,
					yAxisID: 'y'
				},
				{
					label: 'Insider net $',
					data: ordered.map((r) => ({ x: r.week_start, y: r.insider_net_usd / 1e6 })),
					borderColor: '#22c55e',
					backgroundColor: 'transparent',
					borderWidth: 1.5,
					pointRadius: 2,
					tension: 0.1,
					yAxisID: 'y1'
				}
			]
		};
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: { legend: { labels: { color: '#e2e8f0' } } },
		scales: {
			x: {
				type: 'time' as const,
				time: { unit: 'week' as const },
				ticks: { color: '#94a3b8' },
				grid: { color: '#1e293b' }
			},
			y: {
				position: 'left' as const,
				title: { display: true, text: 'Off-exchange $M', color: '#94a3b8' },
				ticks: { color: '#94a3b8', callback: (v: number | string) => `$${Number(v).toFixed(0)}M` },
				grid: { color: '#1e293b' }
			},
			y1: {
				position: 'right' as const,
				title: { display: true, text: 'Insider net $M', color: '#94a3b8' },
				ticks: { color: '#94a3b8', callback: (v: number | string) => `$${Number(v).toFixed(0)}M` },
				grid: { display: false }
			}
		}
	};

	function fmtM(v: number): string {
		const abs = Math.abs(v);
		if (abs >= 1e9) return `${v >= 0 ? '' : '-'}$${(abs / 1e9).toFixed(2)}B`;
		if (abs >= 1e6) return `${v >= 0 ? '' : '-'}$${(abs / 1e6).toFixed(0)}M`;
		if (abs >= 1e3) return `${v >= 0 ? '' : '-'}$${(abs / 1e3).toFixed(0)}k`;
		return `${v >= 0 ? '' : '-'}$${Math.abs(v).toFixed(0)}`;
	}
</script>

<svelte:head><title>Smart vs dumb money · secLab</title></svelte:head>

<h1>Smart vs dumb money <span class="panel-sub">— off-exchange routing + insider flow</span></h1>
<p class="subtitle">
	Per ticker per week, compare dark-pool volume (where institutions hide block trades) against
	non-ATS wholesaler volume (Citadel Securities / Virtu / Jane Street — almost entirely
	internalized retail order flow), overlaid with insider Form 4 net $. The smart side ranks
	tickers with the highest institutional routing share — if insiders are also buying, that's a
	textbook accumulation setup. The dumb side ranks retail-flow-dominant tickers — insider
	selling into that flow is a classic distribution warning.
</p>

{#if error}<div class="error">{error}</div>{/if}

{#if latest?.week_start}
	<div class="meta">Latest week with both feeds: <strong>{latest.week_start}</strong></div>
{/if}

<div class="grid">
	<section class="panel smart-panel">
		<h2>
			Institutional accumulation
			<span class="panel-sub">— highest ATS share · insider buys = strong signal</span>
		</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Ticker</th>
						<th class="num">ATS share</th>
						<th class="num">Dark pool $</th>
						<th class="num">Wholesaler $</th>
						<th class="num">Insider net $</th>
						<th class="num">Buys/Sells</th>
					</tr>
				</thead>
				<tbody>
					{#each latest?.smart ?? [] as r}
						{@const insBullish = r.insider_net_usd > 0 && r.n_buys > 0}
						<tr
							onclick={() => loadDetail(r.ticker)}
							class:selected={selectedTicker === r.ticker}
							class:confirmed={insBullish}
						>
							<td class="ticker">{r.ticker}</td>
							<td class="num strong">{(r.ats_share * 100).toFixed(1)}%</td>
							<td class="num">{fmtM(r.dp_notional)}</td>
							<td class="num muted">{fmtM(r.na_notional)}</td>
							<td class="num" class:up={r.insider_net_usd > 0} class:down={r.insider_net_usd < 0}>
								{r.insider_net_usd ? fmtM(r.insider_net_usd) : '—'}
							</td>
							<td class="num muted">
								{r.n_buys > 0 ? `${r.n_buys}B` : ''}{r.n_buys > 0 && r.n_sells > 0 ? '/' : ''}{r.n_sells > 0 ? `${r.n_sells}S` : ''}
								{#if !r.n_buys && !r.n_sells}—{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section class="panel dumb-panel">
		<h2>
			Distribution warning
			<span class="panel-sub">— retail-flow dominant · insider sells = strong signal</span>
		</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Ticker</th>
						<th class="num">Wholesaler share</th>
						<th class="num">Wholesaler $</th>
						<th class="num">Dark pool $</th>
						<th class="num">Insider net $</th>
						<th class="num">Buys/Sells</th>
					</tr>
				</thead>
				<tbody>
					{#each latest?.dumb ?? [] as r}
						{@const insBearish = r.insider_net_usd < 0 && r.n_sells > 0}
						<tr
							onclick={() => loadDetail(r.ticker)}
							class:selected={selectedTicker === r.ticker}
							class:confirmed={insBearish}
						>
							<td class="ticker">{r.ticker}</td>
							<td class="num strong">{(r.na_share * 100).toFixed(1)}%</td>
							<td class="num">{fmtM(r.na_notional)}</td>
							<td class="num muted">{fmtM(r.dp_notional)}</td>
							<td class="num" class:up={r.insider_net_usd > 0} class:down={r.insider_net_usd < 0}>
								{r.insider_net_usd ? fmtM(r.insider_net_usd) : '—'}
							</td>
							<td class="num muted">
								{r.n_buys > 0 ? `${r.n_buys}B` : ''}{r.n_buys > 0 && r.n_sells > 0 ? '/' : ''}{r.n_sells > 0 ? `${r.n_sells}S` : ''}
								{#if !r.n_buys && !r.n_sells}—{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
</div>

<section class="panel">
	<div class="panel-head">
		<h2>Ticker detail</h2>
		<form
			onsubmit={(e) => {
				e.preventDefault();
				if (tickerInput.trim()) loadDetail(tickerInput.trim().toUpperCase());
			}}
			class="ticker-search"
		>
			<input
				type="text"
				bind:value={tickerInput}
				placeholder="Ticker (e.g. COIN)"
				size="10"
			/>
			<button type="submit" disabled={detailLoading || !tickerInput.trim()}>Look up</button>
		</form>
	</div>
	{#if detailLoading}
		<p class="status">Loading…</p>
	{:else if selectedTicker && series.length > 0}
		<h3>{selectedTicker}</h3>
		{#if tickerChart}
			<div class="chart">
				<Line data={tickerChart} options={chartOptions} />
			</div>
		{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Week</th>
						<th class="num">ATS $</th>
						<th class="num">Wholesaler $</th>
						<th class="num">ATS share</th>
						<th class="num">Insider net $</th>
						<th class="num">B/S</th>
					</tr>
				</thead>
				<tbody>
					{#each series as w}
						<tr>
							<td>{w.week_start}</td>
							<td class="num">{fmtM(w.dp_notional)}</td>
							<td class="num">{fmtM(w.na_notional)}</td>
							<td class="num">{w.ats_share != null ? `${(w.ats_share * 100).toFixed(1)}%` : '—'}</td>
							<td class="num" class:up={w.insider_net_usd > 0} class:down={w.insider_net_usd < 0}>
								{w.insider_net_usd ? fmtM(w.insider_net_usd) : '—'}
							</td>
							<td class="num muted">
								{w.n_buys > 0 ? `${w.n_buys}B` : ''}{w.n_buys > 0 && w.n_sells > 0 ? '/' : ''}{w.n_sells > 0 ? `${w.n_sells}S` : ''}
								{#if !w.n_buys && !w.n_sells}—{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else if selectedTicker}
		<p class="empty">No data for {selectedTicker}.</p>
	{:else}
		<p class="empty">Click any ticker above or type one to inspect its weekly smart/dumb flow.</p>
	{/if}
</section>

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	h2 {
		margin: 0 0 0.75rem;
		font-size: 1.1rem;
	}
	h3 {
		margin: 0.5rem 0;
		font-size: 1rem;
	}
	.panel-sub {
		font-weight: normal;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	.subtitle {
		color: #94a3b8;
		max-width: 820px;
		margin-top: 0;
	}
	.meta {
		padding: 0.5rem 1rem;
		background: #1e293b;
		border-radius: 4px;
		font-size: 0.88rem;
		margin-bottom: 1rem;
		color: #cbd5e1;
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin-bottom: 1.25rem;
	}
	@media (max-width: 1100px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
	.panel {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
	}
	.smart-panel {
		border-left: 3px solid #22c55e;
	}
	.dumb-panel {
		border-left: 3px solid #f87171;
	}
	.panel-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 0.5rem;
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
		padding: 0.4rem 0.7rem;
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
	td.ticker {
		font-family: ui-monospace, monospace;
		font-weight: 600;
	}
	td.strong {
		font-weight: 600;
	}
	td.muted {
		color: #94a3b8;
	}
	tbody tr:hover {
		cursor: pointer;
		background: #1e293b;
	}
	tr.selected {
		background: #1e293b;
	}
	tr.confirmed {
		background: rgba(34, 197, 94, 0.06);
	}
	.dumb-panel tr.confirmed {
		background: rgba(248, 113, 113, 0.06);
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	.chart {
		height: 360px;
		margin-bottom: 1rem;
	}
	.ticker-search {
		display: flex;
		gap: 0.4rem;
	}
	input {
		background: #1e293b;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.35rem 0.6rem;
		border-radius: 4px;
	}
	button {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.4rem 0.9rem;
		border-radius: 4px;
		font-size: 0.88rem;
		cursor: pointer;
	}
	button:disabled {
		background: #475569;
		cursor: default;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
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

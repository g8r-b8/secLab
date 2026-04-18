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

	type LeaderRow = {
		ticker: string;
		short_volume: number;
		total_volume: number;
		short_pct: number;
	};
	type Summary = {
		tickers: number;
		total_volume: number;
		short_volume: number;
		avg_short_pct: number;
	};
	type Latest = { date: string | null; summary?: Summary; leaderboard: LeaderRow[] };
	type Spike = {
		ticker: string;
		latest_short_pct: number;
		median_20d_short_pct: number;
		total_volume: number;
		ratio: number;
	};
	type SpikesResp = { date: string | null; spikes: Spike[] };
	type SeriesPt = {
		date: string;
		short_volume: number;
		short_exempt: number;
		total_volume: number;
		short_pct: number;
	};
	type TickerResp = {
		ticker: string;
		series: SeriesPt[];
		latest_vs_20d_median_ratio: number | null;
	};

	let latest = $state<Latest | null>(null);
	let spikes = $state<Spike[]>([]);
	let loading = $state(false);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let error = $state<string | null>(null);

	let minVolume = $state(1_000_000);
	let tickerInput = $state('');
	let selectedTicker = $state<string | null>(null);
	let detail = $state<TickerResp | null>(null);
	let detailLoading = $state(false);

	async function loadLatest() {
		loading = true;
		error = null;
		try {
			const [lr, sp] = await Promise.all([
				fetch(`/api/signals/short-sale?top=50&min_total_volume=${minVolume}`),
				fetch(`/api/signals/short-sale/spikes?top=30&min_total_volume=${minVolume}`)
			]);
			if (!lr.ok) throw new Error(`${lr.status} ${lr.statusText}`);
			latest = await lr.json();
			if (sp.ok) {
				const body: SpikesResp = await sp.json();
				spikes = body.spikes ?? [];
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function refreshIngest() {
		refreshing = true;
		refreshStatus = 'Pulling FINRA Reg SHO daily files…';
		try {
			const r = await fetch('/api/signals/short-sale/refresh?days=10', { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			refreshStatus =
				`Pulled ${body.rows ?? 0} rows across ${body.days ?? 0} days (skipped ${body.skipped ?? 0} cached).`;
			await loadLatest();
		} catch (e) {
			refreshStatus = `Refresh failed: ${e instanceof Error ? e.message : e}`;
		} finally {
			refreshing = false;
		}
	}

	async function loadDetail(ticker: string) {
		selectedTicker = ticker;
		detailLoading = true;
		detail = null;
		try {
			const r = await fetch(`/api/signals/short-sale/${encodeURIComponent(ticker)}?days=60`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			detail = await r.json();
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
		if (!detail?.series?.length) return null;
		const ordered = [...detail.series].reverse();
		return {
			datasets: [
				{
					label: 'Short % of total volume',
					data: ordered.map((r) => ({ x: r.date, y: r.short_pct * 100 })),
					borderColor: '#f87171',
					backgroundColor: 'rgba(248, 113, 113, 0.12)',
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
				time: { unit: 'day' as const },
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

	function fmtBig(v: number): string {
		if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
		if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
		if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
		return `${v.toFixed(0)}`;
	}
</script>

<svelte:head><title>Short-sale volume · secLab</title></svelte:head>

<h1>Short-sale daily volume <span class="panel-sub">— FINRA Reg SHO consolidated</span></h1>
<p class="subtitle">
	Per-ticker daily short % of total FINRA-reported volume, from the Reg SHO daily file
	(CNMSshvol). Elevated short % can signal: hedging against a rally, bearish positioning, or
	squeeze risk building. Spike detection compares the latest day's short % against the
	trailing 20-day median — large ratios flag unusual short-selling activity.
</p>

{#if error}<div class="error">{error}</div>{/if}

<section class="controls">
	{#if latest?.date}
		<span class="meta">
			Latest: <strong>{latest.date}</strong>
			{#if latest.summary}
				· {latest.summary.tickers.toLocaleString()} tickers
				· {fmtBig(latest.summary.total_volume)} total vol
				· avg short % = {(latest.summary.avg_short_pct * 100).toFixed(1)}%
			{/if}
		</span>
	{:else if !loading}
		<span class="meta empty">No data yet — hit Refresh to pull the last 10 days.</span>
	{/if}
	<label class="min-vol">
		Min volume
		<input
			type="number"
			bind:value={minVolume}
			step="100000"
			min="0"
			onchange={() => loadLatest()}
		/>
	</label>
	<button onclick={refreshIngest} disabled={refreshing}>
		{refreshing ? 'Fetching…' : 'Refresh (last 10 days)'}
	</button>
	{#if refreshStatus}
		<span class="status">{refreshStatus}</span>
	{/if}
</section>

{#if latest?.leaderboard?.length}
	<div class="grid">
		<section class="panel">
			<h2>Highest short % <span class="panel-sub">— latest day, ≥ {fmtBig(minVolume)} vol</span></h2>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th class="num">Short %</th>
							<th class="num">Short vol</th>
							<th class="num">Total vol</th>
						</tr>
					</thead>
					<tbody>
						{#each latest.leaderboard as r}
							<tr
								onclick={() => loadDetail(r.ticker)}
								class:selected={selectedTicker === r.ticker}
							>
								<td class="ticker">{r.ticker}</td>
								<td class="num strong" class:hot={r.short_pct >= 0.6}>
									{(r.short_pct * 100).toFixed(1)}%
								</td>
								<td class="num">{fmtBig(r.short_volume)}</td>
								<td class="num muted">{fmtBig(r.total_volume)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<section class="panel">
			<h2>Spikes <span class="panel-sub">— today's short % vs 20-day median</span></h2>
			{#if spikes.length === 0}
				<p class="empty">Need ≥20 days per ticker. Refresh a few times across different days.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Ticker</th>
								<th class="num">Today</th>
								<th class="num">20d median</th>
								<th class="num">Ratio</th>
							</tr>
						</thead>
						<tbody>
							{#each spikes as s}
								<tr
									onclick={() => loadDetail(s.ticker)}
									class:selected={selectedTicker === s.ticker}
								>
									<td class="ticker">{s.ticker}</td>
									<td class="num strong">{(s.latest_short_pct * 100).toFixed(1)}%</td>
									<td class="num muted">{(s.median_20d_short_pct * 100).toFixed(1)}%</td>
									<td class="num strong hot">{s.ratio.toFixed(1)}×</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</div>
{/if}

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
			<input type="text" bind:value={tickerInput} placeholder="Ticker" size="10" />
			<button type="submit" disabled={detailLoading || !tickerInput.trim()}>Look up</button>
		</form>
	</div>
	{#if detailLoading}
		<p class="status">Loading…</p>
	{:else if detail && detail.series.length > 0}
		<h3>
			{detail.ticker}
			{#if detail.latest_vs_20d_median_ratio != null}
				<span
					class="ratio"
					class:up={detail.latest_vs_20d_median_ratio >= 2}
					class:normal={detail.latest_vs_20d_median_ratio < 2}
				>
					{detail.latest_vs_20d_median_ratio.toFixed(2)}× vs 20d median
				</span>
			{/if}
		</h3>
		{#if tickerChart}
			<div class="chart"><Line data={tickerChart} options={chartOptions} /></div>
		{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Date</th>
						<th class="num">Short %</th>
						<th class="num">Short vol</th>
						<th class="num">Total vol</th>
					</tr>
				</thead>
				<tbody>
					{#each detail.series as d}
						<tr>
							<td>{d.date}</td>
							<td class="num strong" class:hot={d.short_pct >= 0.6}>
								{(d.short_pct * 100).toFixed(1)}%
							</td>
							<td class="num">{fmtBig(d.short_volume)}</td>
							<td class="num muted">{fmtBig(d.total_volume)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else if selectedTicker}
		<p class="empty">No data for {selectedTicker}.</p>
	{:else}
		<p class="empty">Click any ticker above or type one to see its daily short-sale history.</p>
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
		display: flex;
		align-items: center;
		gap: 0.75rem;
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
	.controls {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin-bottom: 1rem;
		flex-wrap: wrap;
	}
	.meta {
		color: #cbd5e1;
		font-size: 0.88rem;
	}
	.meta.empty {
		font-style: italic;
	}
	.min-vol {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.85rem;
		color: #94a3b8;
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
	.grid {
		display: grid;
		grid-template-columns: 2fr 1fr;
		gap: 1rem;
	}
	@media (max-width: 1000px) {
		.grid {
			grid-template-columns: 1fr;
		}
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
	td.hot {
		color: #f87171;
	}
	tbody tr:hover {
		cursor: pointer;
		background: #1e293b;
	}
	tr.selected {
		background: #1e293b;
	}
	.ratio {
		font-size: 0.9rem;
		padding: 0.2rem 0.6rem;
		border-radius: 3px;
	}
	.ratio.up {
		background: rgba(248, 113, 113, 0.2);
		color: #f87171;
	}
	.ratio.normal {
		background: #1e293b;
		color: #94a3b8;
	}
	.chart {
		height: 320px;
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
		font-size: 0.85rem;
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

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
		Tooltip,
		Legend,
		Filler
	} from 'chart.js';

	ChartJS.register(
		LineElement,
		LineController,
		PointElement,
		CategoryScale,
		LinearScale,
		Tooltip,
		Legend,
		Filler
	);

	type Curve = { day: number; mean_cum_return: number; median_cum_return: number; n: number };
	type HalflifeResult = {
		preset: string;
		label?: string;
		curve?: Curve[];
		peak_day?: number;
		peak_cum_return?: number;
		half_life_days?: number | null;
		front_loaded_pct?: number | null;
		n_signals_used?: number;
		n_signals_total?: number;
		max_days?: number;
		error?: string;
	};
	type Preset = { key: string; label: string };

	const PALETTE = [
		'#22c55e', '#60a5fa', '#f59e0b', '#ec4899', '#a78bfa',
		'#14b8a6', '#f87171', '#eab308', '#0ea5e9', '#c084fc'
	];

	let presets = $state<Preset[]>([]);
	let selected = $state<Set<string>>(new Set(['cluster_buys', 'csuite_buys']));
	let start = $state('2022-01-01');
	let end = $state('2024-12-31');
	let maxDays = $state(60);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let results = $state<HalflifeResult[]>([]);

	async function loadPresets() {
		const r = await fetch('/api/backtest/presets');
		if (r.ok) presets = (await r.json()).presets ?? [];
	}

	function togglePreset(key: string) {
		const next = new Set(selected);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		selected = next;
	}

	async function run() {
		if (selected.size === 0) {
			error = 'Select at least one preset.';
			return;
		}
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				presets: [...selected].join(','),
				start,
				end,
				max_days: String(maxDays)
			});
			const r = await fetch(`/api/research/halflife?${params}`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			results = body.results ?? [];
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			results = [];
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		untrack(() => {
			loadPresets();
			run();
		});
	});

	const overlayChart = $derived.by(() => {
		const datasets = results
			.filter((r) => !r.error && r.curve)
			.map((r, i) => ({
				label: r.preset,
				data: r.curve!.map((c) => ({ x: c.day, y: c.mean_cum_return * 100 })),
				borderColor: PALETTE[i % PALETTE.length],
				backgroundColor: 'transparent',
				borderWidth: 2,
				pointRadius: 0,
				tension: 0.15
			}));
		return { datasets };
	});

	const chartOptions = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: { legend: { display: true, labels: { color: '#cbd5e1' } } },
		scales: {
			x: {
				type: 'linear' as const,
				title: { display: true, text: 'Days since entry', color: '#94a3b8' },
				ticks: { color: '#94a3b8' },
				grid: { color: '#1e293b' }
			},
			y: {
				title: { display: true, text: 'Cumulative return %', color: '#94a3b8' },
				ticks: {
					color: '#94a3b8',
					callback: (v: number | string) => `${Number(v).toFixed(1)}%`
				},
				grid: { color: '#1e293b' }
			}
		}
	};
</script>

<svelte:head><title>Signal half-life · secLab</title></svelte:head>

<h1>Signal half-life comparison</h1>
<p class="subtitle">
	For each preset, compute the cross-sectional mean forward-return curve from entry (T+1 close)
	through entry+N days. Reveals which signals front-load (pop fast, fade fast) vs slow-build
	(accumulate over weeks). Directly informs optimal hold_days per signal.
</p>

{#if error}
	<div class="error">{error}</div>
{/if}

<section class="controls">
	<div class="preset-grid">
		{#each presets as p}
			<label class="checkbox">
				<input
					type="checkbox"
					checked={selected.has(p.key)}
					onchange={() => togglePreset(p.key)}
				/>
				<span title={p.label}>{p.key}</span>
			</label>
		{/each}
	</div>
	<div class="inputs">
		<label>
			<span>Start</span>
			<input type="date" bind:value={start} />
		</label>
		<label>
			<span>End</span>
			<input type="date" bind:value={end} />
		</label>
		<label>
			<span>Max days</span>
			<input type="number" min="5" max="252" step="5" bind:value={maxDays} />
		</label>
		<button onclick={run} disabled={loading}>
			{loading ? 'Running…' : 'Run'}
		</button>
	</div>
</section>

{#if results.length > 0}
	<section class="panel">
		<div class="chart">
			<Line data={overlayChart} options={chartOptions} />
		</div>
	</section>

	<section class="panel">
		<h2>Summary</h2>
		<table>
			<thead>
				<tr>
					<th>Preset</th>
					<th class="num">Signals</th>
					<th class="num">Peak day</th>
					<th class="num">Peak %</th>
					<th class="num">Half-life</th>
					<th class="num">Day-1 / peak</th>
					<th>Shape</th>
				</tr>
			</thead>
			<tbody>
				{#each results as r}
					{#if r.error}
						<tr class="muted">
							<td>{r.preset}</td>
							<td colspan="6"><em>{r.error}</em> {r.n_signals_total ? `(${r.n_signals_total} signals)` : ''}</td>
						</tr>
					{:else}
						{@const peakPct = (r.peak_cum_return ?? 0) * 100}
						{@const flPct = ((r.front_loaded_pct ?? 0) * 100)}
						<tr>
							<td class="preset-name">{r.preset}</td>
							<td class="num">{r.n_signals_used}</td>
							<td class="num">{r.peak_day}</td>
							<td class="num" class:up={peakPct >= 0} class:down={peakPct < 0}>
								{peakPct >= 0 ? '+' : ''}{peakPct.toFixed(2)}%
							</td>
							<td class="num">
								{r.half_life_days != null ? r.half_life_days.toFixed(1) + 'd' : '>' + r.max_days + 'd'}
							</td>
							<td class="num">{flPct.toFixed(0)}%</td>
							<td>
								{#if (r.peak_day ?? 999) <= 5}
									<span class="tag fast">fast</span>
								{:else if (r.peak_day ?? 999) <= 20}
									<span class="tag med">medium</span>
								{:else}
									<span class="tag slow">slow-build</span>
								{/if}
								{#if flPct >= 50}
									<span class="tag frontloaded">front-loaded</span>
								{/if}
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	</section>

	<section class="panel tip">
		<h2>Interpretation</h2>
		<ul>
			<li>
				<strong>Fast + front-loaded</strong> signals (cluster_buys, tender_offers): short holds
				(1-5 days) capture most of the alpha with minimal market exposure. Tight profit targets
				work.
			</li>
			<li>
				<strong>Slow-build</strong> signals (csuite_buys, schedule_13d, piotroski_long): need
				weeks to play out. Hold 20-60 days and resist stop-losses triggering on market noise.
			</li>
			<li>
				<strong>Half-life</strong> tells you post-peak decay rate. Long half-life = durable
				alpha; short = mean-reversion setting in.
			</li>
		</ul>
	</section>
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	h2 {
		margin: 0 0 0.75rem;
		font-size: 1.1rem;
	}
	.subtitle {
		color: #94a3b8;
		max-width: 720px;
		margin-top: 0;
	}
	.controls {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
	}
	.preset-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 0.5rem 1rem;
		margin-bottom: 1rem;
	}
	.checkbox {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.88rem;
		color: #cbd5e1;
		cursor: pointer;
	}
	.inputs {
		display: flex;
		gap: 1rem;
		align-items: flex-end;
		flex-wrap: wrap;
	}
	.inputs label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	input {
		background: #1e293b;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	button {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.55rem 1.25rem;
		border-radius: 4px;
		font-size: 0.9rem;
		cursor: pointer;
	}
	button:disabled {
		background: #475569;
		cursor: default;
	}
	.panel {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
	}
	.chart {
		height: 360px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.88rem;
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 0.45rem 0.75rem;
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
	.preset-name {
		font-weight: 600;
	}
	.muted td {
		color: #64748b;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	.tag {
		display: inline-block;
		padding: 0.1rem 0.5rem;
		border-radius: 3px;
		font-size: 0.75rem;
		margin-right: 0.25rem;
	}
	.tag.fast {
		background: rgba(34, 197, 94, 0.2);
		color: #4ade80;
	}
	.tag.med {
		background: rgba(245, 158, 11, 0.2);
		color: #f59e0b;
	}
	.tag.slow {
		background: rgba(96, 165, 250, 0.2);
		color: #60a5fa;
	}
	.tag.frontloaded {
		background: rgba(168, 85, 247, 0.2);
		color: #c084fc;
	}
	.tip ul {
		margin: 0;
		padding-left: 1.25rem;
		line-height: 1.8;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
		margin-bottom: 1rem;
	}
</style>

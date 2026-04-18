<script lang="ts">
	import { Chart as ChartJS, registerables } from 'chart.js';
	import 'chartjs-adapter-date-fns';
	import { onDestroy } from 'svelte';

	ChartJS.register(...registerables);

	type Props = {
		ticker: string;
		start: string; // YYYY-MM-DD — buy window start
		end: string; // YYYY-MM-DD — buy window end
		padDays?: number; // days to pad price chart around the window
	};

	let { ticker, start, end, padDays = 30 }: Props = $props();

	type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };
	type Buy = {
		date: string;
		issuer: string;
		shares: number;
		price: number;
		value: number;
		insider_name: string;
		position: string;
	};
	type Sell = {
		filing_date: string;
		approx_sale_date: string | null;
		seller_name: string;
		relationship: string;
		shares: number;
		aggregate_value: number;
	};
	type Info = {
		sector: string;
		industry: string;
		long_name: string;
		market_cap: number | null;
		exchange: string;
		currency: string;
		website: string;
	};

	let bars = $state<Bar[]>([]);
	let buys = $state<Buy[]>([]);
	let sells = $state<Sell[]>([]);
	let info = $state<Info | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let canvas: HTMLCanvasElement | undefined = $state();
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	let chart: any;

	const addDays = (iso: string, n: number): string => {
		const d = new Date(iso);
		d.setDate(d.getDate() + n);
		return d.toISOString().slice(0, 10);
	};

	async function load() {
		if (!ticker) return;
		loading = true;
		error = null;
		const priceStart = addDays(start, -padDays);
		const priceEnd = addDays(end, padDays);
		try {
			const [pRes, bRes, sRes] = await Promise.all([
				fetch(
					`/api/prices/${encodeURIComponent(ticker)}?start=${priceStart}&end=${priceEnd}`
				),
				fetch(
					`/api/tickers/${encodeURIComponent(ticker)}/insider-buys?start=${start}&end=${end}`
				),
				fetch(`/api/tickers/${encodeURIComponent(ticker)}/form144?start=${start}&end=${end}`)
			]);
			if (!pRes.ok) {
				let msg = `prices ${pRes.status}`;
				try {
					const b = await pRes.json();
					if (b?.detail) msg = String(b.detail);
				} catch {
					// keep default
				}
				throw new Error(msg);
			}
			if (!bRes.ok) throw new Error(`buys ${bRes.status}`);
			const pJson = await pRes.json();
			const bJson = await bRes.json();
			bars = pJson.bars ?? [];
			info = pJson.info ?? null;
			buys = bJson.buys ?? [];
			sells = sRes.ok ? (await sRes.json()).sells ?? [] : [];
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			bars = [];
			buys = [];
			sells = [];
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// Track the reactive keys so effect re-fires when they change
		ticker;
		start;
		end;
		load();
	});

	$effect(() => {
		if (!canvas || !bars.length) return;

		const closes = bars.map((b) => ({ x: b.date, y: b.close }));
		const radiusFor = (v: number) => Math.max(5, Math.min(20, Math.log10(Math.max(v, 1)) * 2.5));
		const buyPoints = buys.map((b) => ({ x: b.date, y: b.price, meta: b }));
		const buyRadii = buys.map((b) => radiusFor(b.value));

		// Form 144 has no exact execution price — use aggregate_value / shares as
		// the implied price and plot the marker on the filing_date.
		const sellPoints = sells.map((s) => ({
			x: s.filing_date,
			y: s.shares > 0 ? s.aggregate_value / s.shares : 0,
			meta: s
		}));
		const sellRadii = sells.map((s) => radiusFor(s.aggregate_value));

		chart?.destroy();
		chart = new ChartJS(canvas, {
			type: 'line',
			data: {
				datasets: [
					{
						label: 'Close',
						data: closes,
						borderColor: '#60a5fa',
						backgroundColor: 'rgba(96, 165, 250, 0.08)',
						borderWidth: 2,
						pointRadius: 0,
						tension: 0.15,
						fill: true,
						order: 3
					},
					{
						label: 'Insider buy',
						// Same dataset type as the line so both share the y-scale.
						// showLine:false + per-point radii == a scatter overlay.
						data: buyPoints,
						showLine: false,
						pointRadius: buyRadii,
						pointHoverRadius: buyRadii.map((r) => r + 3),
						pointBackgroundColor: 'rgba(34, 197, 94, 0.7)',
						pointBorderColor: '#22c55e',
						pointBorderWidth: 2,
						order: 2
					},
					{
						label: 'Planned sell (Form 144)',
						data: sellPoints,
						showLine: false,
						pointRadius: sellRadii,
						pointHoverRadius: sellRadii.map((r) => r + 3),
						pointStyle: 'rectRot',
						pointBackgroundColor: 'rgba(248, 113, 113, 0.7)',
						pointBorderColor: '#ef4444',
						pointBorderWidth: 2,
						order: 1
					}
				]
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				interaction: { mode: 'nearest', intersect: false },
				plugins: {
					legend: { labels: { color: '#e2e8f0' } },
					tooltip: {
						callbacks: {
							label: (ctx) => {
								const money = (v: number) =>
									v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${(v / 1e3).toFixed(0)}K`;
								if (ctx.dataset.label === 'Insider buy') {
									const raw = ctx.raw as { meta?: Buy };
									const m = raw?.meta;
									if (!m) return '';
									return [
										`BUY — ${m.insider_name} — ${m.position || '—'}`,
										`${m.shares.toLocaleString()} sh @ $${m.price.toFixed(2)} = ${money(m.value)}`
									];
								}
								if (ctx.dataset.label === 'Planned sell (Form 144)') {
									const raw = ctx.raw as { meta?: Sell; y: number };
									const m = raw?.meta;
									if (!m) return '';
									const approx = m.approx_sale_date ? ` (approx sale ${m.approx_sale_date})` : '';
									return [
										`PLANNED SELL — ${m.seller_name} — ${m.relationship || '—'}`,
										`${m.shares.toLocaleString()} sh · implied $${raw.y.toFixed(2)} = ${money(m.aggregate_value)}${approx}`
									];
								}
								return `Close $${(ctx.raw as { y: number }).y.toFixed(2)}`;
							}
						}
					}
				},
				scales: {
					x: {
						type: 'time',
						time: { unit: 'day', displayFormats: { day: 'MMM d' } },
						ticks: { color: '#94a3b8' },
						grid: { color: '#1e293b' }
					},
					y: {
						ticks: {
							color: '#94a3b8',
							callback: (v: number | string) => `$${Number(v).toFixed(2)}`
						},
						grid: { color: '#1e293b' }
					}
				}
			}
		});
	});

	onDestroy(() => chart?.destroy());

	const totalBought = $derived(buys.reduce((s, b) => s + b.value, 0));
	const totalPlannedSell = $derived(sells.reduce((s, r) => s + (r.aggregate_value || 0), 0));
	const firstPrice = $derived(bars[0]?.close);
	const lastPrice = $derived(bars[bars.length - 1]?.close);
	const pct = $derived(
		firstPrice && lastPrice ? ((lastPrice - firstPrice) / firstPrice) * 100 : 0
	);
	const fmtMoney = (n: number) =>
		n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${(n / 1e3).toFixed(0)}K`;
</script>

<div class="wrap">
	<header>
		<div class="title">
			<h3>{ticker}{info?.long_name ? ` — ${info.long_name}` : ''}</h3>
			{#if info?.sector || info?.industry}
				<div class="meta">
					{#if info.sector}<span class="tag sector">{info.sector}</span>{/if}
					{#if info.industry}<span class="tag industry">{info.industry}</span>{/if}
					{#if info.exchange}<span class="tag ex">{info.exchange}</span>{/if}
					{#if info.market_cap}
						<span class="tag cap">
							{info.market_cap >= 1e9
								? `$${(info.market_cap / 1e9).toFixed(1)}B`
								: `$${(info.market_cap / 1e6).toFixed(0)}M`}
							cap
						</span>
					{/if}
				</div>
			{/if}
		</div>
		<div class="stats">
			{#if buys.length}
				<span class="buy-stat">
					● {buys.length} buy{buys.length === 1 ? '' : 's'} / {fmtMoney(totalBought)}
				</span>
			{/if}
			{#if sells.length}
				<span class="sell-stat">
					▼ {sells.length} planned sell{sells.length === 1 ? '' : 's'} / {fmtMoney(totalPlannedSell)}
				</span>
			{/if}
			{#if firstPrice && lastPrice}
				<span class:up={pct >= 0} class:down={pct < 0}>
					{pct >= 0 ? '+' : ''}{pct.toFixed(1)}% over window
				</span>
			{/if}
		</div>
	</header>

	{#if loading}
		<div class="status">Loading {ticker}…</div>
	{:else if error}
		<div class="error">
			{error}
			{#if error.includes('rate-lim') || error.includes('503')}
				<br />Tip: add <code>TIINGO_API_KEY</code> to <code>.env</code> for a fallback
				(tiingo.com — 30s free signup).
			{/if}
		</div>
	{:else if !bars.length}
		<div class="status">
			No price data for {ticker} in this window. Could be delisted, a warrant/unit that Yahoo
			doesn't cover, or a recent IPO outside the date range.
		</div>
	{/if}

	<div class="chart"><canvas bind:this={canvas}></canvas></div>
</div>

<style>
	.wrap {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
		margin-top: 1rem;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		margin-bottom: 0.75rem;
		gap: 1rem;
		flex-wrap: wrap;
	}
	.title {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.meta {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.tag {
		background: #0f172a;
		border: 1px solid #334155;
		color: #94a3b8;
		font-size: 0.7rem;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.tag.sector {
		color: #bfdbfe;
		border-color: #1e3a8a;
	}
	.tag.industry {
		color: #bbf7d0;
		border-color: #166534;
	}
	h3 {
		margin: 0;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.stats {
		display: flex;
		gap: 0.75rem;
		color: #94a3b8;
		font-size: 0.85rem;
		flex-wrap: wrap;
	}
	.buy-stat {
		color: #4ade80;
	}
	.sell-stat {
		color: #f87171;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	.chart {
		height: 360px;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
		margin-bottom: 0.5rem;
	}
	.error {
		color: #fecaca;
		background: #7f1d1d;
		padding: 0.5rem;
		border-radius: 4px;
	}
</style>

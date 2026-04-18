<script lang="ts">
	import { untrack } from 'svelte';

	type FtdRow = {
		ticker: string;
		description: string;
		shares: number;
		price: number;
		notional: number;
		n_rows: number;
	};
	type ThresholdRow = { ticker: string; market: string; issuer: string; cusip: string };
	type SiRow = {
		ticker: string;
		shares_short: number;
		shares_short_prev: number;
		change_pct: number;
		avg_daily_volume: number;
		days_to_cover: number;
	};

	let tab = $state<'ftd' | 'threshold' | 'short_interest'>('ftd');

	let ftd = $state<{ date: string | null; rows: FtdRow[] } | null>(null);
	let threshold = $state<{ date: string | null; rows: ThresholdRow[] } | null>(null);
	let si = $state<{ settlement_date: string | null; rows: SiRow[] } | null>(null);

	let loading = $state(false);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let error = $state<string | null>(null);

	async function loadActive() {
		loading = true;
		error = null;
		try {
			if (tab === 'ftd' && ftd === null) {
				const r = await fetch('/api/signals/ftd?top=50');
				if (r.ok) ftd = await r.json();
			} else if (tab === 'threshold' && threshold === null) {
				const r = await fetch('/api/signals/sho-threshold');
				if (r.ok) threshold = await r.json();
			} else if (tab === 'short_interest' && si === null) {
				const r = await fetch('/api/signals/short-interest?top=50&min_days_to_cover=3');
				if (r.ok) si = await r.json();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function refreshActive() {
		refreshing = true;
		refreshStatus = null;
		try {
			let endpoint = '';
			if (tab === 'ftd') endpoint = '/api/signals/ftd/refresh?months=3';
			else if (tab === 'threshold') endpoint = '/api/signals/sho-threshold/refresh?days=14';
			else endpoint = '/api/signals/short-interest/refresh?months=3';
			const r = await fetch(endpoint, { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			refreshStatus = `Ingested: ${JSON.stringify(body)}`;
			// Force reload active tab
			if (tab === 'ftd') ftd = null;
			else if (tab === 'threshold') threshold = null;
			else si = null;
			await loadActive();
		} catch (e) {
			refreshStatus = `Refresh failed: ${e instanceof Error ? e.message : e}`;
		} finally {
			refreshing = false;
		}
	}

	$effect(() => {
		tab;
		untrack(() => loadActive());
	});

	function fmtBig(v: number): string {
		if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
		if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
		if (v >= 1e3) return `${(v / 1e3).toFixed(0)}k`;
		return `${v.toFixed(0)}`;
	}
	function fmtUsd(v: number): string {
		if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
		if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
		if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
		return `$${v.toFixed(0)}`;
	}
</script>

<svelte:head><title>Short pressure · secLab</title></svelte:head>

<h1>Short pressure <span class="panel-sub">— fails, thresholds, short interest</span></h1>
<p class="subtitle">
	Three complementary short-squeeze signals: <strong>Fails-to-deliver (FTDs)</strong> = SEC
	bi-monthly data on settlement failures (high FTD + tight float = squeeze precursor);
	<strong>Reg SHO Threshold List</strong> = named list of tickers with persistent FTDs (most
	actionable signal in our data); <strong>Weekly Short Interest</strong> = FINRA bi-monthly
	official short shares + days-to-cover. Use together: a ticker on the threshold list with
	rising days-to-cover and growing FTDs is the textbook squeeze setup.
</p>

{#if error}<div class="error">{error}</div>{/if}

<div class="tabs">
	<button class="tab" class:active={tab === 'ftd'} onclick={() => (tab = 'ftd')}>
		Fails-to-deliver
	</button>
	<button class="tab" class:active={tab === 'threshold'} onclick={() => (tab = 'threshold')}>
		Reg SHO Threshold
	</button>
	<button class="tab" class:active={tab === 'short_interest'} onclick={() => (tab = 'short_interest')}>
		Short interest
	</button>
</div>

<section class="controls">
	<button onclick={refreshActive} disabled={refreshing}>
		{refreshing ? 'Fetching…' : 'Refresh this tab'}
	</button>
	{#if refreshStatus}<span class="status">{refreshStatus}</span>{/if}
</section>

{#if loading}
	<p class="status">Loading…</p>
{:else if tab === 'ftd'}
	{#if ftd?.date}
		<p class="meta">Latest settlement: <strong>{ftd.date}</strong> — top by $ fails</p>
	{/if}
	{#if ftd?.rows?.length}
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th>Description</th>
							<th class="num">Shares failed</th>
							<th class="num">Price</th>
							<th class="num">Notional</th>
						</tr>
					</thead>
					<tbody>
						{#each ftd.rows as r}
							<tr>
								<td class="ticker">{r.ticker}</td>
								<td class="desc">{r.description}</td>
								<td class="num">{fmtBig(r.shares)}</td>
								<td class="num">${r.price.toFixed(2)}</td>
								<td class="num strong">{fmtUsd(r.notional)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{:else}
		<p class="empty">No FTD data yet. Hit "Refresh this tab" to pull the last 3 months.</p>
	{/if}
{:else if tab === 'threshold'}
	{#if threshold?.date}
		<p class="meta">
			Latest threshold list: <strong>{threshold.date}</strong>
			({threshold.rows.length} tickers on the list — persistent failures to deliver)
		</p>
	{/if}
	{#if threshold?.rows?.length}
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th>Issuer</th>
							<th>Market</th>
							<th>CUSIP</th>
						</tr>
					</thead>
					<tbody>
						{#each threshold.rows as r}
							<tr>
								<td class="ticker">{r.ticker}</td>
								<td>{r.issuer}</td>
								<td class="muted">{r.market}</td>
								<td class="muted mono">{r.cusip}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{:else}
		<p class="empty">No threshold data yet. Hit Refresh to pull the last 14 days.</p>
	{/if}
{:else if tab === 'short_interest'}
	{#if si?.settlement_date}
		<p class="meta">
			Latest settlement: <strong>{si.settlement_date}</strong> — top by days-to-cover
			(filtered ≥3d, outliers ≥999 excluded)
		</p>
	{/if}
	{#if si?.rows?.length}
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th class="num">Days to cover</th>
							<th class="num">Shares short</th>
							<th class="num">Δ vs prior</th>
							<th class="num">Avg daily vol</th>
						</tr>
					</thead>
					<tbody>
						{#each si.rows as r}
							<tr>
								<td class="ticker">{r.ticker}</td>
								<td class="num strong hot">{r.days_to_cover.toFixed(2)}</td>
								<td class="num">{fmtBig(r.shares_short)}</td>
								<td class="num" class:up={r.change_pct >= 0} class:down={r.change_pct < 0}>
									{r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(1)}%
								</td>
								<td class="num muted">{fmtBig(r.avg_daily_volume)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{:else}
		<p class="empty">No short-interest data yet. Hit Refresh to pull the last 3 months.</p>
	{/if}
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
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
	.tabs {
		display: flex;
		gap: 0.5rem;
		margin: 0.5rem 0 1rem;
	}
	.tab {
		background: transparent;
		border: 1px solid #334155;
		color: #94a3b8;
		padding: 0.45rem 1rem;
		border-radius: 4px;
		font-size: 0.88rem;
		cursor: pointer;
	}
	.tab:hover:not(.active) {
		border-color: #475569;
		color: #cbd5e1;
	}
	.tab.active {
		background: #1e293b;
		border-color: #60a5fa;
		color: #e2e8f0;
	}
	.controls {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.5rem 0;
		margin-bottom: 1rem;
	}
	button {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.45rem 1rem;
		border-radius: 4px;
		font-size: 0.88rem;
		cursor: pointer;
	}
	button:disabled {
		background: #475569;
		cursor: default;
	}
	.meta {
		padding: 0.5rem 1rem;
		background: #1e293b;
		border-radius: 4px;
		font-size: 0.88rem;
		margin: 0 0 1rem;
		color: #cbd5e1;
	}
	.panel {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
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
	td.mono {
		font-family: ui-monospace, monospace;
	}
	td.desc {
		max-width: 280px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	td.muted {
		color: #94a3b8;
	}
	td.strong {
		font-weight: 600;
	}
	td.hot {
		color: #f87171;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
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

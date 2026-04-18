<script lang="ts">
	import { untrack } from 'svelte';

	type LeaderRow = {
		ticker: string;
		shares: number;
		trades: number;
		notional: number;
		n_ats: number;
	};
	type LatestResponse = {
		week_start: string | null;
		summary?: {
			rows: number;
			total_shares: number;
			total_notional: number;
			unique_tickers: number;
			venues: number;
		};
		leaderboard: LeaderRow[];
	};
	type Spike = {
		ticker: string;
		latest_shares: number;
		median_8w_shares: number;
		ratio: number;
	};
	type SpikesResponse = { week_start: string | null; spikes: Spike[] };

	type WeekPoint = {
		week_start: string;
		shares: number;
		trades: number;
		notional: number;
		n_ats: number;
	};
	type AtsRow = {
		mpid: string;
		ats_name: string;
		shares: number;
		trades: number;
		notional: number;
	};
	type TickerResponse = {
		ticker: string;
		series: WeekPoint[];
		latest_vs_8w_median_ratio: number | null;
		by_ats_last_90d: AtsRow[];
	};

	// ATS = dark pools. non_ats = OTC wholesalers (Citadel Securities, Virtu,
	// etc.) — off-exchange "retail flow" internalization. Same table shape,
	// different meaning.
	let venue = $state<'ats' | 'non_ats'>('ats');
	const VENUE_LABEL: Record<string, string> = {
		ats: 'ATS (dark pools)',
		non_ats: 'OTC non-ATS (wholesalers / retail flow)'
	};

	let latest = $state<LatestResponse | null>(null);
	let spikes = $state<Spike[]>([]);
	let loading = $state(false);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let error = $state<string | null>(null);

	let tickerInput = $state('');
	let selectedTicker = $state<string | null>(null);
	let tickerDetail = $state<TickerResponse | null>(null);
	let detailLoading = $state(false);

	function apiRoot(): string {
		return venue === 'ats' ? '/api/signals/dark-pool' : '/api/signals/non-ats';
	}

	async function loadLatest() {
		loading = true;
		error = null;
		try {
			const root = apiRoot();
			const [lr, sp] = await Promise.all([
				fetch(`${root}?top=50`),
				fetch(`${root}/spikes?top=30`)
			]);
			if (!lr.ok) throw new Error(`${lr.status} ${lr.statusText}`);
			latest = await lr.json();
			if (sp.ok) {
				const body: SpikesResponse = await sp.json();
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
		refreshStatus = `Pulling FINRA ${VENUE_LABEL[venue]} — 30-90s per new week…`;
		try {
			const r = await fetch(`${apiRoot()}/refresh?weeks=12`, { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			refreshStatus =
				`Pulled ${body.rows ?? 0} rows across ${body.weeks ?? 0} weeks (skipped ${body.skipped ?? 0} already cached).`;
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
		tickerDetail = null;
		try {
			const r = await fetch(`${apiRoot()}/${encodeURIComponent(ticker)}?weeks=26`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			tickerDetail = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			detailLoading = false;
		}
	}

	// Reload when venue tab changes
	$effect(() => {
		venue;
		selectedTicker = null;
		tickerDetail = null;
		untrack(() => loadLatest());
	});

	function fmtBigS(v: number): string {
		if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
		if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
		if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
		return `$${v.toFixed(0)}`;
	}
	function fmtBig(v: number): string {
		if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
		if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
		if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
		return `${v.toFixed(0)}`;
	}
</script>

<svelte:head><title>Dark-pool volume · secLab</title></svelte:head>

<h1>Off-exchange flow <span class="panel-sub">— FINRA weekly per-ticker</span></h1>
<p class="subtitle">
	Weekly off-exchange volume from FINRA. Two complementary views:
	<strong>ATS (dark pools)</strong> show institutional block trading;
	<strong>OTC non-ATS (wholesalers)</strong> show retail order-flow internalization
	via Citadel Securities, Virtu, Jane Street, etc. Spike detection ranks tickers whose
	latest-week share volume deviates most from their trailing 8-week median.
</p>

<div class="tabs">
	<button class="tab" class:active={venue === 'ats'} onclick={() => (venue = 'ats')}>
		ATS (dark pools)
	</button>
	<button class="tab" class:active={venue === 'non_ats'} onclick={() => (venue = 'non_ats')}>
		OTC non-ATS (wholesalers / retail flow)
	</button>
</div>

{#if error}
	<div class="error">{error}</div>
{/if}

<section class="controls">
	{#if latest?.week_start}
		<span class="meta">
			Latest week: <strong>{latest.week_start}</strong>
			{#if latest.summary}
				· {latest.summary.unique_tickers.toLocaleString()} tickers
				· {fmtBigS(latest.summary.total_notional)} notional
				· {latest.summary.venues} ATSs
			{/if}
		</span>
	{:else if !loading}
		<span class="meta empty">No data yet — hit Refresh to pull the last 12 weeks.</span>
	{/if}
	<button onclick={refreshIngest} disabled={refreshing}>
		{refreshing ? 'Fetching…' : 'Refresh (last 12 weeks)'}
	</button>
	{#if refreshStatus}
		<span class="status">{refreshStatus}</span>
	{/if}
</section>

{#if latest?.leaderboard?.length}
	<div class="grid">
		<section class="panel">
			<h2>Top 50 tickers by {venue === 'ats' ? 'dark-pool' : 'wholesaler'} $ notional <span class="panel-sub">— latest week</span></h2>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th class="num">Shares</th>
							<th class="num">Trades</th>
							<th class="num">Notional</th>
							<th class="num"># ATS</th>
						</tr>
					</thead>
					<tbody>
						{#each latest.leaderboard as r}
							<tr
								onclick={() => loadDetail(r.ticker)}
								class:selected={selectedTicker === r.ticker}
							>
								<td class="ticker">{r.ticker}</td>
								<td class="num">{fmtBig(r.shares)}</td>
								<td class="num">{fmtBig(r.trades)}</td>
								<td class="num strong">{fmtBigS(r.notional)}</td>
								<td class="num">{r.n_ats}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<section class="panel">
			<h2>Spikes <span class="panel-sub">— latest week vs 8-week median share volume</span></h2>
			{#if spikes.length === 0}
				<p class="empty">
					Need ≥8 weeks of data per ticker before spikes compute. Come back after a few refreshes.
				</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Ticker</th>
								<th class="num">This week</th>
								<th class="num">8w median</th>
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
									<td class="num">{fmtBig(s.latest_shares)}</td>
									<td class="num">{fmtBig(s.median_8w_shares)}</td>
									<td class="num strong up">{s.ratio.toFixed(1)}×</td>
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
			<input
				type="text"
				bind:value={tickerInput}
				placeholder="Ticker (e.g. NVDA)"
				size="10"
			/>
			<button type="submit" disabled={detailLoading || !tickerInput.trim()}>Look up</button>
		</form>
	</div>
	{#if detailLoading}
		<p class="status">Loading…</p>
	{:else if tickerDetail}
		<h3>
			{tickerDetail.ticker}
			{#if tickerDetail.latest_vs_8w_median_ratio != null}
				<span
					class="ratio"
					class:up={tickerDetail.latest_vs_8w_median_ratio >= 2}
					class:normal={tickerDetail.latest_vs_8w_median_ratio < 2}
				>
					{tickerDetail.latest_vs_8w_median_ratio.toFixed(2)}× vs 8w median
				</span>
			{/if}
		</h3>
		{#if tickerDetail.series.length === 0}
			<p class="empty">No dark-pool activity recorded for {tickerDetail.ticker}.</p>
		{:else}
			<div class="grid">
				<div>
					<h4>Weekly series</h4>
					<div class="table-wrap">
						<table>
							<thead>
								<tr>
									<th>Week</th>
									<th class="num">Shares</th>
									<th class="num">Trades</th>
									<th class="num">Notional</th>
									<th class="num">ATSs</th>
								</tr>
							</thead>
							<tbody>
								{#each tickerDetail.series as w}
									<tr>
										<td>{w.week_start}</td>
										<td class="num">{fmtBig(w.shares)}</td>
										<td class="num">{fmtBig(w.trades)}</td>
										<td class="num">{fmtBigS(w.notional)}</td>
										<td class="num">{w.n_ats}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
				<div>
					<h4>By ATS (last 90 days)</h4>
					<div class="table-wrap">
						<table>
							<thead>
								<tr>
									<th>MPID</th>
									<th>ATS</th>
									<th class="num">Shares</th>
									<th class="num">Notional</th>
								</tr>
							</thead>
							<tbody>
								{#each tickerDetail.by_ats_last_90d as a}
									<tr>
										<td class="mpid">{a.mpid}</td>
										<td>{a.ats_name}</td>
										<td class="num">{fmtBig(a.shares)}</td>
										<td class="num">{fmtBigS(a.notional)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
			</div>
		{/if}
	{:else}
		<p class="empty">Click any ticker above or type one to inspect its weekly + per-ATS breakdown.</p>
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
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	h4 {
		margin: 0.5rem 0;
		font-size: 0.9rem;
		color: #94a3b8;
	}
	.panel-sub {
		font-weight: normal;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	.subtitle {
		color: #94a3b8;
		max-width: 780px;
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
		padding: 0.4rem 0.75rem;
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
	td.mpid {
		font-family: ui-monospace, monospace;
		color: #94a3b8;
	}
	td.strong {
		font-weight: 600;
	}
	tbody tr:hover {
		background: #1e293b;
		cursor: pointer;
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
		background: rgba(34, 197, 94, 0.18);
		color: #4ade80;
	}
	.ratio.normal {
		background: #1e293b;
		color: #94a3b8;
	}
	.up {
		color: #4ade80;
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
</style>

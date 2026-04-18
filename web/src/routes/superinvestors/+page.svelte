<script lang="ts">
	import PriceImpactChart from '$lib/PriceImpactChart.svelte';

	type Fund = { cik: number; label: string; long_name: string };

	type Position = {
		cusip: string;
		issuer_name: string;
		title_of_class: string;
		put_call: string;
		value_usd: number;
		shares: number;
		prior_value: number;
		prior_shares: number;
		shares_change: number;
		value_change: number;
		delta: 'NEW' | 'LOAD' | 'TRIM' | 'HOLD' | 'EXIT';
		ticker: string | null;
	};

	type Exit = {
		cusip: string;
		title_of_class: string;
		put_call: string;
		prior_value: number;
		prior_shares: number;
		delta: 'EXIT';
	};

	type Holdings = {
		cik: number;
		label: string | null;
		long_name: string | null;
		latest_period: string;
		prior_period: string | null;
		n_positions: number;
		total_value: number;
		holdings: Position[];
		new_positions: Position[];
		loads: Position[];
		trims: Position[];
		exits: Exit[];
		error?: string;
	};

	let funds = $state<Fund[]>([]);
	let selectedCik = $state<number | null>(null);
	let data = $state<Holdings | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let selectedTicker = $state<string | null>(null);

	async function loadFunds() {
		const r = await fetch('/api/signals/13f/funds');
		if (!r.ok) return;
		const j = await r.json();
		funds = j.funds ?? [];
		if (!selectedCik && funds.length) selectedCik = funds[0].cik;
	}
	$effect(() => {
		loadFunds();
	});

	async function loadFund() {
		if (selectedCik == null) return;
		loading = true;
		error = null;
		try {
			const r = await fetch(`/api/signals/13f/${selectedCik}?top=100`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			data = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			data = null;
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		selectedCik;
		loadFund();
	});

	async function refreshAll() {
		refreshing = true;
		refreshStatus = 'Pulling latest 13F filings for all funds…';
		try {
			const r = await fetch('/api/signals/13f/refresh?max_filings=4', { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const j = await r.json();
			const ingested = (j.funds ?? []).reduce(
				(n: number, f: { newly_ingested?: string[] }) =>
					n + (f.newly_ingested?.length ?? 0),
				0
			);
			refreshStatus = `Ingested ${ingested} new quarter(s) across ${j.funds?.length ?? 0} funds.`;
			await loadFund();
		} catch (e) {
			refreshStatus = `Refresh failed: ${e instanceof Error ? e.message : e}`;
		} finally {
			refreshing = false;
		}
	}

	const fmtMoney = (v: number) => {
		const a = Math.abs(v);
		if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
		if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
		if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
		return `$${v.toFixed(0)}`;
	};
	const fmtShares = (v: number) =>
		v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : v.toFixed(0);

	const deltaColor = (d: string) =>
		d === 'NEW' ? '#4ade80' : d === 'LOAD' ? '#86efac' : d === 'TRIM' ? '#fca5a5' : d === 'EXIT' ? '#f87171' : '#94a3b8';
</script>

<svelte:window onkeydown={(e) => selectedTicker && e.key === 'Escape' && (selectedTicker = null)} />

<h1>🏛 Superinvestors (13F)</h1>
<p class="subtitle">
	Quarterly holdings for a curated set of brand-name managers (Buffett, Burry, Ackman, Tepper,
	Druckenmiller, Einhorn, Icahn, Tiger Global, …). New positions and big loads historically produce
	positive abnormal returns — this dash makes them copy-pasteable.
</p>

<section class="controls">
	<label>
		<span>Fund</span>
		<select bind:value={selectedCik}>
			{#each funds as f}
				<option value={f.cik}>{f.label} · {f.long_name}</option>
			{/each}
		</select>
	</label>
	<button class="refresh" onclick={refreshAll} disabled={refreshing}>
		{refreshing ? 'Refreshing…' : 'Refresh all funds'}
	</button>
	{#if refreshStatus}<span class="status">{refreshStatus}</span>{/if}
</section>

{#if error}<div class="error">Error: {error}</div>{/if}

{#if loading}
	<div class="status">Loading holdings…</div>
{:else if data && !data.error}
	<section class="kpis">
		<div class="kpi">
			<span class="kpi-label">Latest period</span>
			<span class="kpi-value">{data.latest_period}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Prior period</span>
			<span class="kpi-value">{data.prior_period || '—'}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Positions</span>
			<span class="kpi-value">{data.n_positions.toLocaleString()}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Portfolio value</span>
			<span class="kpi-value">{fmtMoney(data.total_value)}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">New positions</span>
			<span class="kpi-value up">{data.new_positions.length}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Loads</span>
			<span class="kpi-value up">{data.loads.length}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Trims</span>
			<span class="kpi-value down">{data.trims.length}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Exits</span>
			<span class="kpi-value down">{data.exits.length}</span>
		</div>
	</section>

	<div class="grid">
		<section class="panel highlight">
			<h2>New positions <span class="panel-sub">(↑ freshly added this quarter)</span></h2>
			{#if data.new_positions.length === 0}
				<p class="empty">No new positions this quarter.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Issuer</th>
								<th class="num">Value</th>
								<th class="num">Shares</th>
							</tr>
						</thead>
						<tbody>
							{#each data.new_positions as p}
								<tr
									class:clickable={!!p.ticker}
									class:selected={p.ticker === selectedTicker}
									onclick={() => p.ticker && (selectedTicker = p.ticker)}
								>
									<td>
										{#if p.ticker}<span class="ticker">{p.ticker}</span>{/if}
										<span class="name">{p.issuer_name}</span>
										{#if p.put_call}<span class="pc">{p.put_call}</span>{/if}
									</td>
									<td class="num">{fmtMoney(p.value_usd)}</td>
									<td class="num">{fmtShares(p.shares)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<section class="panel">
			<h2>Loads <span class="panel-sub">(existing positions increased ≥25%)</span></h2>
			{#if data.loads.length === 0}
				<p class="empty">No significant loads this quarter.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Issuer</th>
								<th class="num">Value</th>
								<th class="num">Δ Shares</th>
								<th class="num">% change</th>
							</tr>
						</thead>
						<tbody>
							{#each data.loads as p}
								{@const pct = p.prior_shares > 0 ? (p.shares - p.prior_shares) / p.prior_shares : 0}
								<tr
									class:clickable={!!p.ticker}
									class:selected={p.ticker === selectedTicker}
									onclick={() => p.ticker && (selectedTicker = p.ticker)}
								>
									<td>
										{#if p.ticker}<span class="ticker">{p.ticker}</span>{/if}
										<span class="name">{p.issuer_name}</span>
									</td>
									<td class="num">{fmtMoney(p.value_usd)}</td>
									<td class="num up">+{fmtShares(p.shares_change)}</td>
									<td class="num up">+{(pct * 100).toFixed(0)}%</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</div>

	<div class="grid">
		<section class="panel">
			<h2>Trims + Exits</h2>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Issuer</th>
							<th>Action</th>
							<th class="num">Value</th>
							<th class="num">% change</th>
						</tr>
					</thead>
					<tbody>
						{#each [...data.trims, ...data.exits] as p}
							{@const shares = (p as Position).shares ?? 0}
							{@const pct = p.prior_shares > 0 ? (shares - p.prior_shares) / p.prior_shares : -1}
							<tr
								class:clickable={!!(p as Position).ticker}
								onclick={() => (p as Position).ticker && (selectedTicker = (p as Position).ticker)}
							>
								<td>
									{#if (p as Position).ticker}<span class="ticker">{(p as Position).ticker}</span>{/if}
									<span class="name">{(p as Position).issuer_name ?? ''}</span>
								</td>
								<td>
									<span class="delta-badge" style="background:{deltaColor(p.delta)}20;color:{deltaColor(p.delta)}">
										{p.delta}
									</span>
								</td>
								<td class="num">{fmtMoney((p as Position).value_usd ?? 0)}</td>
								<td class="num down">{(pct * 100).toFixed(0)}%</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<section class="panel">
			<h2>Full portfolio</h2>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Issuer</th>
							<th class="num">Value</th>
							<th>Action</th>
						</tr>
					</thead>
					<tbody>
						{#each data.holdings as p}
							<tr
								class:clickable={!!p.ticker}
								class:selected={p.ticker === selectedTicker}
								onclick={() => p.ticker && (selectedTicker = p.ticker)}
							>
								<td>
									{#if p.ticker}<span class="ticker">{p.ticker}</span>{/if}
									<span class="name">{p.issuer_name}</span>
								</td>
								<td class="num">{fmtMoney(p.value_usd)}</td>
								<td>
									<span class="delta-badge" style="background:{deltaColor(p.delta)}20;color:{deltaColor(p.delta)}">{p.delta}</span>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	</div>
{:else if data?.error}
	<div class="warning">
		{data.error} — click <strong>Refresh all funds</strong> above to fetch the latest 13Fs
		(~30 seconds per fund).
	</div>
{/if}

{#if selectedTicker}
	<button
		class="overlay-backdrop"
		onclick={() => (selectedTicker = null)}
		aria-label="Close detail view"
	></button>
	<div class="modal" role="dialog" aria-modal="true" aria-label="{selectedTicker} holding detail">
		<header class="modal-header">
			<h2>{selectedTicker} <span class="modal-sub">— held by {data?.label}</span></h2>
			<button class="close" onclick={() => (selectedTicker = null)} aria-label="Close">✕</button>
		</header>
		<div class="modal-body">
			<PriceImpactChart
				ticker={selectedTicker}
				start={data?.prior_period || data?.latest_period || '2024-01-01'}
				end={data?.latest_period || '2026-04-17'}
				padDays={60}
			/>
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
		max-width: 760px;
	}
	.controls {
		display: flex;
		gap: 1rem;
		align-items: center;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1.5rem 0;
		flex-wrap: wrap;
	}
	.controls label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.8rem;
		color: #94a3b8;
	}
	.controls select {
		background: #0f172a;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
		min-width: 320px;
	}
	.refresh {
		background: transparent;
		border: 1px solid #334155;
		color: #cbd5e1;
		padding: 0.5rem 1rem;
		border-radius: 4px;
		cursor: pointer;
	}
	.kpis {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	.kpi {
		background: #1e293b;
		padding: 0.85rem;
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
		font-size: 1.3rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	@media (max-width: 1000px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
	.panel {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
	}
	.panel.highlight {
		border: 1px solid #166534;
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
	.table-wrap {
		overflow: auto;
		max-height: 500px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.4rem 0.6rem;
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
	tr.clickable {
		cursor: pointer;
	}
	tr.clickable:hover {
		background: #0f172a;
	}
	tr.selected {
		background: #1e3a8a40;
	}
	.ticker {
		font-weight: 600;
		color: #60a5fa;
		margin-right: 0.5rem;
	}
	.name {
		color: #cbd5e1;
	}
	.pc {
		margin-left: 0.35rem;
		font-size: 0.7rem;
		padding: 0.05rem 0.3rem;
		border-radius: 3px;
		background: #7f1d1d;
		color: #fecaca;
		text-transform: uppercase;
	}
	.delta-badge {
		display: inline-block;
		font-size: 0.72rem;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		font-weight: 600;
		letter-spacing: 0.04em;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
		font-size: 0.85rem;
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
		padding: 0.75rem 1rem;
		border-radius: 4px;
	}
	.overlay-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.72);
		border: none;
		cursor: pointer;
		z-index: 100;
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
		z-index: 101;
	}
	.modal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 1rem 1.25rem;
		border-bottom: 1px solid #1e293b;
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
	}
	.modal-body {
		padding: 1rem 1.25rem 1.5rem;
	}
</style>

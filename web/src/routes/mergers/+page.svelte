<script lang="ts">
	import { untrack } from 'svelte';

	type Filing = {
		accession: string;
		form: string;
		filing_date: string;
		issuer_cik: number | null;
		issuer_name: string;
		issuer_ticker: string | null;
		filer_cik: number | null;
		filer_name: string;
		primary_doc: string;
	};
	type ByIssuer = {
		issuer_cik: number;
		issuer_name: string;
		issuer_ticker: string | null;
		last_filing: string;
		n_filings: number;
		prem_count: number;
		defm_count: number;
		going_private_count: number;
		status: 'preliminary' | 'definitive' | 'going private';
	};
	type Resp = {
		range: { start: string; end: string };
		summary: { total_filings: number };
		filings: Filing[];
		by_issuer: ByIssuer[];
	};

	let data = $state<Resp | null>(null);
	let loading = $state(false);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const r = await fetch('/api/signals/mergers?top=500');
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			data = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function refreshIngest() {
		refreshing = true;
		refreshStatus = 'Scraping EDGAR for last 120 days of merger proxies…';
		try {
			const r = await fetch('/api/signals/mergers/refresh?days=120', { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			refreshStatus = `Ingested ${body.rows ?? 0} filings across ${body.days ?? 0} days (skipped ${body.skipped ?? 0} cached).`;
			await load();
		} catch (e) {
			refreshStatus = `Refresh failed: ${e instanceof Error ? e.message : e}`;
		} finally {
			refreshing = false;
		}
	}

	$effect(() => {
		untrack(() => load());
	});

	function statusColor(s: string): string {
		return s === 'going private' ? 'status-private' : s === 'definitive' ? 'status-def' : 'status-prem';
	}
</script>

<svelte:head><title>M&A tracker · secLab</title></svelte:head>

<h1>M&amp;A tracker <span class="panel-sub">— merger proxies + going-private</span></h1>
<p class="subtitle">
	Live tape of material deal announcements from EDGAR: <strong>PREM14A</strong> (preliminary
	merger proxy, first public disclosure), <strong>DEFM14A</strong> (definitive — shareholder vote
	set), <strong>SC 13E-3</strong> (going-private / management buyout). Preliminary → definitive
	progression tells you where a deal is in its lifecycle.
</p>

{#if error}<div class="error">{error}</div>{/if}

<section class="controls">
	{#if data?.range}
		<span class="meta">
			Showing <strong>{data.summary.total_filings}</strong> filings from
			<strong>{data.range.start}</strong> to <strong>{data.range.end}</strong>
		</span>
	{/if}
	<button onclick={refreshIngest} disabled={refreshing}>
		{refreshing ? 'Fetching…' : 'Refresh (last 120d)'}
	</button>
	{#if refreshStatus}<span class="status">{refreshStatus}</span>{/if}
</section>

{#if loading}
	<p class="status">Loading…</p>
{:else if data?.by_issuer?.length}
	<section class="panel">
		<h2>By target company <span class="panel-sub">— preliminary → definitive progression</span></h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Target</th>
						<th>Ticker</th>
						<th>Status</th>
						<th class="num">Prelim</th>
						<th class="num">Definitive</th>
						<th class="num">Going-private</th>
						<th class="num">Last filing</th>
					</tr>
				</thead>
				<tbody>
					{#each data.by_issuer as r}
						<tr>
							<td class="name">{r.issuer_name}</td>
							<td class="ticker">{r.issuer_ticker ?? '—'}</td>
							<td><span class="pill {statusColor(r.status)}">{r.status}</span></td>
							<td class="num">{r.prem_count || '—'}</td>
							<td class="num">{r.defm_count || '—'}</td>
							<td class="num">{r.going_private_count || '—'}</td>
							<td class="num">{r.last_filing}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section class="panel">
		<h2>All filings <span class="panel-sub">— reverse chronological</span></h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Date</th>
						<th>Form</th>
						<th>Target</th>
						<th>Filer</th>
					</tr>
				</thead>
				<tbody>
					{#each data.filings as f}
						<tr>
							<td class="num">{f.filing_date}</td>
							<td><span class="form-tag">{f.form}</span></td>
							<td class="name">
								{f.issuer_name}
								{#if f.issuer_ticker}<span class="ticker-inline">({f.issuer_ticker})</span>{/if}
							</td>
							<td class="filer">{f.filer_name || '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{:else if data}
	<p class="empty">No merger filings yet. Hit Refresh to pull the last 120 days.</p>
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
		padding: 0.5rem 0;
		margin-bottom: 1rem;
		flex-wrap: wrap;
	}
	.meta {
		color: #cbd5e1;
		font-size: 0.88rem;
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
	.ticker-inline {
		font-family: ui-monospace, monospace;
		color: #94a3b8;
		margin-left: 0.5rem;
	}
	td.name {
		max-width: 420px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	td.filer {
		max-width: 300px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: #94a3b8;
	}
	.pill {
		display: inline-block;
		padding: 0.15rem 0.5rem;
		border-radius: 3px;
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.status-prem {
		background: rgba(96, 165, 250, 0.18);
		color: #60a5fa;
	}
	.status-def {
		background: rgba(34, 197, 94, 0.18);
		color: #4ade80;
	}
	.status-private {
		background: rgba(251, 146, 60, 0.2);
		color: #fb923c;
	}
	.form-tag {
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
		color: #cbd5e1;
		background: #1e293b;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
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

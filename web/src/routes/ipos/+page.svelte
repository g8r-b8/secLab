<script lang="ts">
	import { untrack } from 'svelte';

	type PipelineRow = {
		issuer_cik: number;
		issuer_name: string;
		issuer_ticker: string | null;
		first_s1_date: string | null;
		last_s1_date: string | null;
		n_amendments: number;
		last_pricing_date: string | null;
	};
	type RecentRow = {
		issuer_cik: number;
		issuer_name: string;
		issuer_ticker: string | null;
		pricing_date: string;
		first_s1_date: string | null;
	};
	type LockupRow = {
		issuer_cik: number;
		issuer_name: string;
		issuer_ticker: string | null;
		pricing_date: string;
		lockup_date: string;
		days_until: number;
	};

	let tab = $state<'pipeline' | 'recent' | 'lockups'>('pipeline');

	let pipeline = $state<{ rows: PipelineRow[]; window_days: number } | null>(null);
	let recent = $state<{ rows: RecentRow[]; window_days: number } | null>(null);
	let lockups = $state<{ rows: LockupRow[]; from: string; to: string } | null>(null);

	let loading = $state(false);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);
	let error = $state<string | null>(null);

	async function loadActive() {
		loading = true;
		error = null;
		try {
			if (tab === 'pipeline' && !pipeline) {
				const r = await fetch('/api/signals/ipos/pipeline?days=60&top=100');
				if (r.ok) pipeline = await r.json();
			} else if (tab === 'recent' && !recent) {
				const r = await fetch('/api/signals/ipos/recent?days=180&top=100');
				if (r.ok) recent = await r.json();
			} else if (tab === 'lockups' && !lockups) {
				const r = await fetch('/api/signals/ipos/lockups?window_days=90&top=200');
				if (r.ok) lockups = await r.json();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function refreshIngest() {
		refreshing = true;
		refreshStatus = 'Scraping EDGAR for last 240 days of S-1 / 424B / 8-A…';
		try {
			const r = await fetch('/api/signals/ipos/refresh?days=240', { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			refreshStatus = `Ingested ${body.rows ?? 0} filings across ${body.days ?? 0} days (skipped ${body.skipped ?? 0} cached).`;
			pipeline = null;
			recent = null;
			lockups = null;
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
</script>

<svelte:head><title>IPO tracker · secLab</title></svelte:head>

<h1>IPO tracker <span class="panel-sub">— pipeline · pricings · lockup calendar</span></h1>
<p class="subtitle">
	Tracks IPO activity via EDGAR S-1 (initial registration), 424B (final prospectus at pricing),
	and 8-A (exchange listing) filings. The lockup calendar derives from pricing date + 180 days
	— a predictable supply shock when insider + VC shares unlock. Short-side setups on lockup-
	expiration day have well-documented forward-return evidence.
</p>

{#if error}<div class="error">{error}</div>{/if}

<div class="tabs">
	<button class="tab" class:active={tab === 'pipeline'} onclick={() => (tab = 'pipeline')}>
		Pipeline (S-1 filers)
	</button>
	<button class="tab" class:active={tab === 'recent'} onclick={() => (tab = 'recent')}>
		Recent IPOs (priced)
	</button>
	<button class="tab" class:active={tab === 'lockups'} onclick={() => (tab = 'lockups')}>
		Lockup expirations
	</button>
</div>

<section class="controls">
	<button onclick={refreshIngest} disabled={refreshing}>
		{refreshing ? 'Fetching…' : 'Refresh (last 240 days)'}
	</button>
	{#if refreshStatus}<span class="status">{refreshStatus}</span>{/if}
</section>

{#if loading}
	<p class="status">Loading…</p>
{:else if tab === 'pipeline'}
	{#if pipeline?.rows?.length}
		<p class="meta">
			<strong>{pipeline.rows.length}</strong> issuers with an S-1 in the last {pipeline.window_days}d
			and no pricing filing yet.
		</p>
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Issuer</th>
							<th>Ticker</th>
							<th class="num">First S-1</th>
							<th class="num">Last S-1/A</th>
							<th class="num">Amendments</th>
						</tr>
					</thead>
					<tbody>
						{#each pipeline.rows as r}
							<tr>
								<td class="name">{r.issuer_name}</td>
								<td class="ticker">{r.issuer_ticker ?? '—'}</td>
								<td class="num">{r.first_s1_date}</td>
								<td class="num">{r.last_s1_date}</td>
								<td class="num" class:hot={r.n_amendments >= 3}>{r.n_amendments}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="panel-sub" style="margin-top:0.5rem">
				High amendment count = active roadshow, likely pricing soon.
			</p>
		</section>
	{:else}
		<p class="empty">No pipeline data yet. Hit Refresh to pull 240 days of S-1 activity.</p>
	{/if}
{:else if tab === 'recent'}
	{#if recent?.rows?.length}
		<p class="meta">
			<strong>{recent.rows.length}</strong> priced IPOs (424B4 filings) in the last {recent.window_days}d.
		</p>
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th>Issuer</th>
							<th class="num">Priced</th>
							<th class="num">First S-1</th>
						</tr>
					</thead>
					<tbody>
						{#each recent.rows as r}
							<tr>
								<td class="ticker">{r.issuer_ticker ?? '—'}</td>
								<td class="name">{r.issuer_name}</td>
								<td class="num strong">{r.pricing_date}</td>
								<td class="num muted">{r.first_s1_date ?? '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{:else}
		<p class="empty">No recent IPO data. Hit Refresh.</p>
	{/if}
{:else if tab === 'lockups'}
	{#if lockups?.rows?.length}
		<p class="meta">
			Upcoming 180-day lockup expirations from <strong>{lockups.from}</strong> to
			<strong>{lockups.to}</strong> — {lockups.rows.length} issuers.
		</p>
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th>Issuer</th>
							<th class="num">Priced</th>
							<th class="num">Lockup expires</th>
							<th class="num">Days</th>
						</tr>
					</thead>
					<tbody>
						{#each lockups.rows as r}
							<tr class:imminent={r.days_until <= 14}>
								<td class="ticker">{r.issuer_ticker ?? '—'}</td>
								<td class="name">{r.issuer_name}</td>
								<td class="num muted">{r.pricing_date}</td>
								<td class="num strong">{r.lockup_date}</td>
								<td class="num" class:hot={r.days_until <= 14}>{r.days_until}d</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="panel-sub" style="margin-top:0.5rem">
				Rows within 14 days are highlighted — these are the imminent supply shocks.
			</p>
		</section>
	{:else}
		<p class="empty">
			No upcoming lockups. Either you haven't ingested 180+ days of IPO data (hit Refresh) or
			nothing is about to unlock in this window.
		</p>
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
	td.name {
		max-width: 420px;
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
		font-weight: 600;
	}
	tr.imminent {
		background: rgba(248, 113, 113, 0.06);
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

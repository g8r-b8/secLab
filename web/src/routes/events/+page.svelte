<script lang="ts">
	import PriceImpactChart from '$lib/PriceImpactChart.svelte';

	type ItemMeta = { code: string; label: string; default_on: boolean };

	type Filing = {
		accession: string;
		filing_date: string;
		issuer_cik: number | null;
		issuer_name: string;
		issuer_ticker: string | null;
		items: string[];
		primary_doc: string;
	};

	type IssuerRow = {
		issuer_name: string;
		issuer_ticker: string | null;
		issuer_cik: number | null;
		n_filings: number;
		items_seen: string[];
		latest: string;
	};

	type EventsResponse = {
		range: { start: string; end: string };
		items_filter: string[] | null;
		coverage?: { missing_days?: number; ingested_days?: string[]; pending_days?: number };
		warnings?: string[];
		summary: { total_filings: number; unique_issuers: number; items_active: number };
		item_counts: Record<string, number>;
		filings: Filing[];
		by_issuer: IssuerRow[];
	};

	// Highlighted items that move stocks materially
	const ITEM_TONE: Record<string, 'bear' | 'bull' | 'neutral'> = {
		'1.03': 'bear', // bankruptcy
		'3.01': 'bear', // delisting
		'4.01': 'bear', // auditor change
		'4.02': 'bear', // restatement
		'5.02': 'bear', // exec departure (usually)
		'2.06': 'bear', // impairment
		'8.01': 'bull', // often buybacks / positive
		'2.01': 'bull', // M&A complete
		'1.01': 'bull', // material agreement
		'2.02': 'neutral' // earnings
	};

	const today = new Date();
	const todayIso = today.toISOString().slice(0, 10);
	const defaultStart = new Date(today.getTime() - 7 * 24 * 3600 * 1000)
		.toISOString()
		.slice(0, 10);

	let itemsMeta = $state<ItemMeta[]>([]);
	let selectedItems = $state<Set<string>>(new Set());
	let start = $state(defaultStart);
	let end = $state(todayIso);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let data = $state<EventsResponse | null>(null);
	let selectedTicker = $state<string | null>(null);

	async function loadMeta() {
		const r = await fetch('/api/signals/8k/items');
		if (!r.ok) return;
		const j = await r.json();
		itemsMeta = j.items ?? [];
		// Seed defaults if nothing selected yet
		if (selectedItems.size === 0) {
			selectedItems = new Set(itemsMeta.filter((i) => i.default_on).map((i) => i.code));
		}
	}
	$effect(() => {
		loadMeta();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				start,
				end,
				items: Array.from(selectedItems).join(',')
			});
			const r = await fetch(`/api/signals/8k?${params}`);
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
		// re-fetch when the selection set or dates change
		selectedItems;
		load();
	});

	function toggleItem(code: string) {
		const next = new Set(selectedItems);
		if (next.has(code)) next.delete(code);
		else next.add(code);
		selectedItems = next;
	}

	function edgarUrl(f: Filing): string {
		if (!f.issuer_cik || !f.accession) return '#';
		const nodash = f.accession.replace(/-/g, '');
		return `https://www.sec.gov/Archives/edgar/data/${f.issuer_cik}/${nodash}/${f.primary_doc}`;
	}
</script>

<svelte:window onkeydown={(e) => selectedTicker && e.key === 'Escape' && (selectedTicker = null)} />

<h1>Material Events (8-K)</h1>
<p class="subtitle">
	Every 8-K filing, tagged by SEC item number. Filter to the items that move stocks —
	restatements (4.02), exec departures (5.02), auditor changes (4.01), bankruptcies (1.03),
	buybacks and material announcements (8.01), completed acquisitions (2.01).
</p>

<section class="controls">
	<label>
		<span>Start</span>
		<input type="date" bind:value={start} />
	</label>
	<label>
		<span>End</span>
		<input type="date" bind:value={end} />
	</label>
	<button onclick={load} disabled={loading}>{loading ? 'Loading…' : 'Run'}</button>
</section>

<section class="chips">
	<div class="chips-header">
		<span>Items:</span>
		<button
			class="chip-action"
			onclick={() =>
				(selectedItems = new Set(itemsMeta.filter((i) => i.default_on).map((i) => i.code)))}
		>
			defaults
		</button>
		<button class="chip-action" onclick={() => (selectedItems = new Set(itemsMeta.map((i) => i.code)))}>
			all
		</button>
		<button class="chip-action" onclick={() => (selectedItems = new Set())}>none</button>
	</div>
	<div class="chip-row">
		{#each itemsMeta as m}
			{@const on = selectedItems.has(m.code)}
			{@const tone = ITEM_TONE[m.code] ?? 'neutral'}
			<button
				class="chip tone-{tone}"
				class:active={on}
				onclick={() => toggleItem(m.code)}
				title={m.label}
			>
				{m.code}
				{#if data?.item_counts[m.code]}<span class="chip-count">{data.item_counts[m.code]}</span>{/if}
			</button>
		{/each}
	</div>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}

{#if data?.warnings?.length}
	{#each data.warnings as w}
		<div class="warning">{w}</div>
	{/each}
{/if}

{#if loading}
	<div class="status">Fetching 8-K filings {start} → {end}…</div>
{:else if data}
	<section class="kpis">
		<div class="kpi">
			<span class="kpi-label">Filings</span>
			<span class="kpi-value">{data.summary.total_filings.toLocaleString()}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Unique issuers</span>
			<span class="kpi-value">{data.summary.unique_issuers.toLocaleString()}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Item types active</span>
			<span class="kpi-value">{data.summary.items_active}</span>
		</div>
	</section>

	<section class="panel">
		<h2>Filing feed ({data.filings.length})</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Filed</th>
						<th>Ticker</th>
						<th>Issuer</th>
						<th>Items</th>
						<th>SEC</th>
					</tr>
				</thead>
				<tbody>
					{#each data.filings as f}
						<tr
							class:clickable={!!f.issuer_ticker}
							class:selected={f.issuer_ticker === selectedTicker}
							onclick={() => f.issuer_ticker && (selectedTicker = f.issuer_ticker)}
						>
							<td>{f.filing_date}</td>
							<td class="ticker">{f.issuer_ticker || '—'}</td>
							<td>{f.issuer_name}</td>
							<td class="items">
								{#each f.items as i}
									{@const tone = ITEM_TONE[i] ?? 'neutral'}
									<span class="item-tag tone-{tone}" title={itemsMeta.find((m) => m.code === i)?.label ?? ''}>{i}</span>
								{/each}
							</td>
							<td>
								<a
									href={edgarUrl(f)}
									target="_blank"
									rel="noopener noreferrer"
									onclick={(e) => e.stopPropagation()}>open</a
								>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/if}

{#if selectedTicker}
	<button
		class="overlay-backdrop"
		onclick={() => (selectedTicker = null)}
		aria-label="Close detail view"
	></button>
	<div class="modal" role="dialog" aria-modal="true" aria-label="{selectedTicker} 8-K impact">
		<header class="modal-header">
			<h2>{selectedTicker} <span class="modal-sub">— 8-K window · {start} → {end}</span></h2>
			<button class="close" onclick={() => (selectedTicker = null)} aria-label="Close">×</button>
		</header>
		<div class="modal-body">
			<PriceImpactChart ticker={selectedTicker} {start} {end} />
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
		align-items: end;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1.5rem 0 0.75rem;
		flex-wrap: wrap;
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
	.chips {
		background: #1e293b;
		padding: 0.75rem 1rem;
		border-radius: 8px;
		margin-bottom: 1rem;
	}
	.chips-header {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin-bottom: 0.5rem;
		font-size: 0.8rem;
		color: #94a3b8;
	}
	.chip-action {
		background: transparent;
		border: 1px solid #334155;
		color: #94a3b8;
		padding: 0.2rem 0.6rem;
		border-radius: 4px;
		font-size: 0.75rem;
		cursor: pointer;
	}
	.chip-action:hover {
		color: #e2e8f0;
	}
	.chip-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		background: #0f172a;
		border: 1px solid #334155;
		color: #94a3b8;
		padding: 0.3rem 0.7rem;
		border-radius: 999px;
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		cursor: pointer;
		transition: border-color 120ms;
	}
	.chip:hover {
		border-color: #60a5fa;
	}
	.chip.active {
		background: #1e3a8a;
		color: #e0f2fe;
		border-color: #60a5fa;
	}
	.chip-count {
		background: rgba(0, 0, 0, 0.35);
		padding: 0 0.35rem;
		border-radius: 999px;
		font-size: 0.7rem;
	}
	.chip.tone-bear {
		color: #fca5a5;
	}
	.chip.tone-bear.active {
		background: #7f1d1d;
		border-color: #f87171;
	}
	.chip.tone-bull {
		color: #86efac;
	}
	.chip.tone-bull.active {
		background: #14532d;
		border-color: #4ade80;
	}
	.kpis {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		margin-bottom: 1rem;
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
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.kpi-value {
		font-size: 1.4rem;
		font-weight: 600;
	}
	.panel {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
	}
	.panel h2 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.table-wrap {
		overflow: auto;
		max-height: 680px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.45rem 0.6rem;
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
	}
	.items {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
	}
	.item-tag {
		background: #0f172a;
		border: 1px solid #334155;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
		font-size: 0.72rem;
		font-variant-numeric: tabular-nums;
	}
	.item-tag.tone-bear {
		color: #fca5a5;
		border-color: #7f1d1d;
	}
	.item-tag.tone-bull {
		color: #86efac;
		border-color: #166534;
	}
	a {
		color: #60a5fa;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
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
		box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
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

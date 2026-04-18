<script lang="ts">
	import PriceImpactChart from '$lib/PriceImpactChart.svelte';

	type Filing = {
		accession: string;
		form: string;
		filing_date: string;
		issuer_cik: number | null;
		issuer_name: string;
		issuer_ticker: string | null;
		primary_doc: string;
	};
	type IssuerRow = {
		issuer_name: string;
		issuer_ticker: string | null;
		issuer_cik: number | null;
		n_filings: number;
		forms: string[];
		latest: string;
	};
	type Response = {
		range: { start: string; end: string };
		warnings?: string[];
		summary: {
			total_filings: number;
			unique_issuers: number;
			form_counts: Record<string, number>;
			repeat_issuers: number;
		};
		filings: Filing[];
		by_issuer: IssuerRow[];
	};

	const today = new Date();
	const todayIso = today.toISOString().slice(0, 10);
	// 30-day default keeps first-load scrape quick (6 form variants for secondaries).
	const defaultStart = new Date(today.getTime() - 30 * 24 * 3600 * 1000)
		.toISOString()
		.slice(0, 10);

	let start = $state(defaultStart);
	let end = $state(todayIso);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let data = $state<Response | null>(null);
	let selectedTicker = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const r = await fetch(`/api/signals/secondaries?start=${start}&end=${end}`);
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
		load();
	});

	function edgarUrl(f: Filing): string {
		if (!f.issuer_cik || !f.accession) return '#';
		const nodash = f.accession.replace(/-/g, '');
		return `https://www.sec.gov/Archives/edgar/data/${f.issuer_cik}/${nodash}/${f.primary_doc}`;
	}

	const FORM_LABELS: Record<string, string> = {
		'S-3': 'Shelf registration',
		'S-3ASR': 'Automatic shelf (WKSI)',
		'S-3/A': 'Shelf amendment',
		'424B5': 'Takedown prospectus',
		'424B7': 'Selling-stockholder takedown',
		'424B2': 'Takedown pricing supplement'
	};
</script>

<svelte:window onkeydown={(e) => selectedTicker && e.key === 'Escape' && (selectedTicker = null)} />

<h1>💧 Secondary Offerings</h1>
<p class="subtitle">
	S-3 shelf registrations (forward-looking inventory) and 424B takedowns (active dilution).
	Announce-day reactions are typically -3% to -10% for full secondaries, smaller for at-the-market
	programs. Repeat offenders (multiple takedowns in a window) are the highest-density bear signal.
</p>

<section class="controls">
	<label><span>Start</span><input type="date" bind:value={start} /></label>
	<label><span>End</span><input type="date" bind:value={end} /></label>
	<button onclick={load} disabled={loading}>{loading ? 'Loading…' : 'Run'}</button>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}
{#if data?.warnings?.length}
	{#each data.warnings as w}<div class="warning">{w}</div>{/each}
{/if}

{#if loading}
	<div class="status">Fetching secondary offerings {start} → {end}…</div>
{:else if data}
	<section class="kpis">
		<div class="kpi">
			<span class="kpi-label">Total filings</span>
			<span class="kpi-value">{data.summary.total_filings.toLocaleString()}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Unique issuers</span>
			<span class="kpi-value">{data.summary.unique_issuers.toLocaleString()}</span>
		</div>
		<div class="kpi highlight">
			<span class="kpi-label">Repeat offenders</span>
			<span class="kpi-value">{data.summary.repeat_issuers.toLocaleString()}</span>
		</div>
		{#each Object.entries(data.summary.form_counts) as [f, n]}
			<div class="kpi small">
				<span class="kpi-label" title={FORM_LABELS[f] ?? ''}>{f}</span>
				<span class="kpi-value">{n}</span>
			</div>
		{/each}
	</section>

	{#if data.by_issuer.some((r) => r.n_filings >= 2)}
		<section class="panel">
			<h2>Repeat offenders <span class="panel-sub">— multiple issuance events in this window</span></h2>
			<div class="table-wrap">
				<table>
					<thead>
						<tr><th>Ticker</th><th>Issuer</th><th class="num">Filings</th><th>Forms</th><th>Latest</th></tr>
					</thead>
					<tbody>
						{#each data.by_issuer.filter((r) => r.n_filings >= 2) as r}
							<tr
								class="clickable bad"
								class:selected={r.issuer_ticker === selectedTicker}
								onclick={() => r.issuer_ticker && (selectedTicker = r.issuer_ticker)}
							>
								<td class="ticker">{r.issuer_ticker || '—'}</td>
								<td>{r.issuer_name}</td>
								<td class="num">{r.n_filings}</td>
								<td class="forms">
									{#each r.forms as f}<span class="form-tag" title={FORM_LABELS[f] ?? ''}>{f}</span>{/each}
								</td>
								<td>{r.latest}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<section class="panel">
		<h2>All filings ({data.filings.length})</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Filed</th><th>Form</th><th>Ticker</th><th>Issuer</th><th>SEC</th></tr>
				</thead>
				<tbody>
					{#each data.filings as f}
						<tr
							class:clickable={!!f.issuer_ticker}
							class:selected={f.issuer_ticker === selectedTicker}
							onclick={() => f.issuer_ticker && (selectedTicker = f.issuer_ticker)}
						>
							<td>{f.filing_date}</td>
							<td><span class="form-tag" title={FORM_LABELS[f.form] ?? ''}>{f.form}</span></td>
							<td class="ticker">{f.issuer_ticker || '—'}</td>
							<td>{f.issuer_name}</td>
							<td><a href={edgarUrl(f)} target="_blank" rel="noopener noreferrer" onclick={(e) => e.stopPropagation()}>open</a></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/if}

{#if selectedTicker}
	<button class="overlay-backdrop" onclick={() => (selectedTicker = null)} aria-label="Close"></button>
	<div class="modal" role="dialog" aria-modal="true">
		<header class="modal-header">
			<h2>{selectedTicker} <span class="modal-sub">— dilution window · {start} → {end}</span></h2>
			<button class="close" onclick={() => (selectedTicker = null)}>✕</button>
		</header>
		<div class="modal-body"><PriceImpactChart ticker={selectedTicker} {start} {end} padDays={60} /></div>
	</div>
{/if}

<style>
	h1 { margin: 0 0 0.25rem; font-size: 1.75rem; }
	.subtitle { color: #94a3b8; margin-top: 0; max-width: 760px; }
	.controls { display: flex; gap: 1rem; align-items: end; padding: 1rem; background: #1e293b; border-radius: 8px; margin: 1.5rem 0; flex-wrap: wrap; }
	.controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8rem; color: #94a3b8; }
	.controls input { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 0.4rem 0.6rem; border-radius: 4px; font-size: 0.9rem; }
	.controls button { background: #2563eb; color: white; border: none; padding: 0.5rem 1.25rem; border-radius: 4px; cursor: pointer; }
	.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
	.kpi { background: #1e293b; padding: 0.85rem; border-radius: 8px; display: flex; flex-direction: column; gap: 0.25rem; }
	.kpi.highlight { background: #7f1d1d80; border: 1px solid #f87171; }
	.kpi.small .kpi-value { font-size: 1.1rem; }
	.kpi-label { color: #94a3b8; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
	.kpi-value { font-size: 1.4rem; font-weight: 600; }
	.panel { background: #1e293b; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }
	.panel h2 { margin: 0 0 0.75rem; font-size: 1rem; color: #e2e8f0; }
	.panel-sub { color: #64748b; font-size: 0.8rem; font-weight: normal; }
	.table-wrap { overflow: auto; max-height: 560px; }
	table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
	th, td { padding: 0.45rem 0.6rem; text-align: left; border-bottom: 1px solid #334155; }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	th { color: #94a3b8; font-weight: 500; position: sticky; top: 0; background: #1e293b; }
	tr.clickable { cursor: pointer; }
	tr.clickable:hover { background: #0f172a; }
	tr.selected { background: #1e3a8a40; }
	tr.bad { background: #7f1d1d1a; }
	.ticker { font-weight: 600; color: #60a5fa; }
	.forms { display: flex; flex-wrap: wrap; gap: 0.25rem; }
	.form-tag { background: #7f1d1d; color: #fecaca; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.7rem; text-transform: uppercase; }
	a { color: #60a5fa; }
	.status { color: #94a3b8; font-style: italic; }
	.error { background: #7f1d1d; color: #fecaca; padding: 0.75rem 1rem; border-radius: 4px; }
	.warning { background: #78350f; color: #fed7aa; padding: 0.6rem 1rem; border-radius: 4px; margin-bottom: 0.5rem; }
	.overlay-backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.72); border: none; cursor: pointer; z-index: 100; }
	.modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #0f172a; border: 1px solid #334155; border-radius: 12px; width: calc(100vw - 4rem); max-width: 1100px; max-height: calc(100vh - 4rem); overflow-y: auto; z-index: 101; }
	.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.25rem; border-bottom: 1px solid #1e293b; }
	.modal-header h2 { margin: 0; font-size: 1.25rem; color: #60a5fa; }
	.modal-sub { color: #94a3b8; font-size: 0.85rem; font-weight: normal; }
	.close { background: transparent; border: 1px solid #334155; color: #cbd5e1; padding: 0.3rem 0.7rem; border-radius: 4px; cursor: pointer; }
	.modal-body { padding: 1rem 1.25rem 1.5rem; }
</style>

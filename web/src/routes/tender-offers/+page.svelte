<script lang="ts">
	import PriceImpactChart from '$lib/PriceImpactChart.svelte';

	type Filing = {
		accession: string;
		form: string;
		filing_date: string;
		subject_cik: number | null;
		subject_name: string;
		subject_ticker: string | null;
		filer_cik: number | null;
		filer_name: string;
		primary_doc: string;
	};
	type TargetRow = {
		subject_name: string;
		subject_ticker: string | null;
		subject_cik: number | null;
		n_filings: number;
		forms: string[];
		acquirers: string[];
		latest: string;
	};
	type Response = {
		range: { start: string; end: string };
		warnings?: string[];
		summary: { total_filings: number; unique_targets: number; form_counts: Record<string, number> };
		filings: Filing[];
		by_target: TargetRow[];
	};

	const FORM_LABELS: Record<string, string> = {
		'SC TO-T': 'Third-party tender offer',
		'SC TO-I': 'Issuer tender offer (buyback)',
		'SC TO-C': 'Tender-offer communications',
		'SC 14D9': 'Target recommendation',
		'SC 14D9/A': 'Target recommendation amendment'
	};

	const today = new Date();
	const todayIso = today.toISOString().slice(0, 10);
	// 30-day default keeps first-load scrape under a minute (5 form variants × pages).
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
			const r = await fetch(`/api/signals/tender-offers?start=${start}&end=${end}`);
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
		if (!f.subject_cik || !f.accession) return '#';
		const nodash = f.accession.replace(/-/g, '');
		return `https://www.sec.gov/Archives/edgar/data/${f.subject_cik}/${nodash}/${f.primary_doc}`;
	}
</script>

<svelte:window onkeydown={(e) => selectedTicker && e.key === 'Escape' && (selectedTicker = null)} />

<h1>Tender Offers (M&A)</h1>
<p class="subtitle">
	Third-party tender offers (<code>SC TO-T</code>), issuer self-tenders/buybacks
	(<code>SC TO-I</code>), and target recommendations (<code>SC 14D9</code>). Binary M&A events
	with pre-announced prices — targets typically snap toward the offer, buybacks compress float.
</p>

<section class="controls">
	<label><span>Start</span><input type="date" bind:value={start} /></label>
	<label><span>End</span><input type="date" bind:value={end} /></label>
	<button onclick={load} disabled={loading}>{loading ? 'Loading…' : 'Run'}</button>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}
{#if data?.warnings?.length}{#each data.warnings as w}<div class="warning">{w}</div>{/each}{/if}

{#if loading}
	<div class="status">
		Fetching tender offers {start} → {end}… <span class="dim">(first load scrapes 5 form variants
		from EDGAR; widen the range only after the initial data is cached locally)</span>
	</div>
{:else if data}
	<section class="kpis">
		<div class="kpi">
			<span class="kpi-label">Total filings</span>
			<span class="kpi-value">{data.summary.total_filings.toLocaleString()}</span>
		</div>
		<div class="kpi">
			<span class="kpi-label">Unique targets</span>
			<span class="kpi-value">{data.summary.unique_targets.toLocaleString()}</span>
		</div>
		{#each Object.entries(data.summary.form_counts) as [f, n]}
			<div class="kpi small">
				<span class="kpi-label" title={FORM_LABELS[f] ?? ''}>{f}</span>
				<span class="kpi-value">{n}</span>
			</div>
		{/each}
	</section>

	<section class="panel">
		<h2>Target companies</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Ticker</th><th>Target</th><th class="num">Filings</th><th>Forms</th><th>Acquirers</th><th>Latest</th></tr>
				</thead>
				<tbody>
					{#each data.by_target as r}
						<tr
							class:clickable={!!r.subject_ticker}
							class:selected={r.subject_ticker === selectedTicker}
							onclick={() => r.subject_ticker && (selectedTicker = r.subject_ticker)}
						>
							<td class="ticker">{r.subject_ticker || '—'}</td>
							<td>{r.subject_name}</td>
							<td class="num">{r.n_filings}</td>
							<td class="forms">{#each r.forms as f}<span class="form-tag" title={FORM_LABELS[f] ?? ''}>{f}</span>{/each}</td>
							<td class="acq">{r.acquirers.join(', ') || '—'}</td>
							<td>{r.latest}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section class="panel">
		<h2>All filings ({data.filings.length})</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Filed</th><th>Form</th><th>Ticker</th><th>Target</th><th>Filer</th><th>SEC</th></tr>
				</thead>
				<tbody>
					{#each data.filings as f}
						<tr
							class:clickable={!!f.subject_ticker}
							class:selected={f.subject_ticker === selectedTicker}
							onclick={() => f.subject_ticker && (selectedTicker = f.subject_ticker)}
						>
							<td>{f.filing_date}</td>
							<td><span class="form-tag" title={FORM_LABELS[f.form] ?? ''}>{f.form}</span></td>
							<td class="ticker">{f.subject_ticker || '—'}</td>
							<td>{f.subject_name}</td>
							<td class="acq">{f.filer_name}</td>
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
			<h2>{selectedTicker} <span class="modal-sub">— tender window · {start} → {end}</span></h2>
			<button class="close" onclick={() => (selectedTicker = null)}>×</button>
		</header>
		<div class="modal-body"><PriceImpactChart ticker={selectedTicker} {start} {end} padDays={60} /></div>
	</div>
{/if}

<style>
	h1 { margin: 0 0 0.25rem; font-size: 1.75rem; }
	.subtitle { color: #94a3b8; margin-top: 0; max-width: 760px; }
	.subtitle code { background: #0f172a; padding: 0.1rem 0.4rem; border-radius: 3px; }
	.controls { display: flex; gap: 1rem; align-items: end; padding: 1rem; background: #1e293b; border-radius: 8px; margin: 1.5rem 0; flex-wrap: wrap; }
	.controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8rem; color: #94a3b8; }
	.controls input { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 0.4rem 0.6rem; border-radius: 4px; font-size: 0.9rem; }
	.controls button { background: #2563eb; color: white; border: none; padding: 0.5rem 1.25rem; border-radius: 4px; cursor: pointer; }
	.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
	.kpi { background: #1e293b; padding: 0.85rem; border-radius: 8px; display: flex; flex-direction: column; gap: 0.25rem; }
	.kpi.small .kpi-value { font-size: 1.1rem; }
	.kpi-label { color: #94a3b8; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
	.kpi-value { font-size: 1.4rem; font-weight: 600; }
	.panel { background: #1e293b; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }
	.panel h2 { margin: 0 0 0.75rem; font-size: 1rem; color: #e2e8f0; }
	.table-wrap { overflow: auto; max-height: 560px; }
	table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
	th, td { padding: 0.45rem 0.6rem; text-align: left; border-bottom: 1px solid #334155; }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	th { color: #94a3b8; font-weight: 500; position: sticky; top: 0; background: #1e293b; }
	tr.clickable { cursor: pointer; }
	tr.clickable:hover { background: #0f172a; }
	tr.selected { background: #1e3a8a40; }
	.ticker { font-weight: 600; color: #60a5fa; }
	.acq { color: #cbd5e1; }
	.forms { display: flex; flex-wrap: wrap; gap: 0.25rem; }
	.form-tag { background: #14532d; color: #bbf7d0; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.7rem; text-transform: uppercase; }
	a { color: #60a5fa; }
	.status { color: #94a3b8; font-style: italic; }
	.dim { color: #64748b; font-size: 0.85em; }
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

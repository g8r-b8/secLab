<script lang="ts">
	import { page } from '$app/state';
	import TickerSearch from '$lib/TickerSearch.svelte';

	type Row = { fy: number | null; period: string | null; value: number };
	type Item = { label: string; uom: string; rows: Row[] };
	type Statements = { IS: Record<string, Item>; BS: Record<string, Item>; CF: Record<string, Item> };
	type Fundamentals = {
		ticker: string;
		cik?: number;
		name?: string;
		statements: Statements;
		latest_quarter?: {
			period: string | null;
			fp: string | null;
			filed: string | null;
			rows: Array<{ tag: string; label: string; stmt: string; value: number; uom: string; period: string | null; fp: string | null }>;
		};
		error?: string;
	};
	type Info = {
		sector: string;
		industry: string;
		long_name: string;
		market_cap: number | null;
		exchange: string;
	};

	const ticker = $derived((page.params.ticker ?? '').toUpperCase());

	type Ratios = {
		price: number | null;
		market_cap: number | null;
		enterprise_value: number | null;
		pe_ratio: number | null;
		pb_ratio: number | null;
		ps_ratio: number | null;
		ev_ebitda: number | null;
		ev_revenue: number | null;
		fcf_yield: number | null;
		earnings_yield: number | null;
		gross_margin: number | null;
		operating_margin: number | null;
		net_margin: number | null;
		roe: number | null;
		roa: number | null;
		debt_to_equity: number | null;
		net_debt: number | null;
		debt_to_ebitda: number | null;
		revenue_growth_yoy: number | null;
		net_income_growth_yoy: number | null;
	};
	type RatiosResponse = {
		ticker: string;
		name?: string;
		period?: string;
		prior_period?: string | null;
		ratios?: Ratios;
		error?: string;
	};

	type DossierResponse = {
		ticker: string;
		cik: number | null;
		range: { start: string; end: string };
		summary: Record<string, number>;
		insider_buys: Array<{ date: string; shares: number; price: number; value: number; insider_name: string; position: string }>;
		planned_sells: Array<{ filing_date: string; approx_sale_date: string | null; seller_name: string; relationship: string; shares: number; aggregate_value: number; broker_name: string }>;
		thirteen_d: Array<{ filing_date: string; form: string; filer_name: string; accession: string }>;
		thirteen_g: Array<{ filing_date: string; form: string; filer_name: string; accession: string }>;
		eight_k: Array<{ filing_date: string; items: string[]; accession: string }>;
		late_filings: Array<{ filing_date: string; form: string; accession: string }>;
		proxy_contests: Array<{ filing_date: string; form: string; filer_name: string; accession: string }>;
		secondaries: Array<{ filing_date: string; form: string; accession: string }>;
		tender_offers: Array<{ filing_date: string; form: string; acquirer: string; accession: string }>;
		form3: Array<{ filing_date: string; filer_name: string; accession: string }>;
		superinvestor_holders: Array<{ fund_label: string; fund_name: string; period_end: string; value_usd: number; shares: number; put_call: string; issuer_name: string }>;
	};

	let data = $state<Fundamentals | null>(null);
	let info = $state<Info | null>(null);
	let ratios = $state<RatiosResponse | null>(null);
	let dossier = $state<DossierResponse | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let years = $state(5);
	let tab = $state<'fundamentals' | 'activity'>('fundamentals');
	// 2-year default window for the SEC activity tab
	const today = new Date();
	let dossierStart = $state(`${today.getFullYear() - 2}-01-01`);
	let dossierEnd = $state(today.toISOString().slice(0, 10));

	async function loadDossier() {
		if (!ticker) return;
		try {
			const r = await fetch(
				`/api/tickers/${encodeURIComponent(ticker)}/dossier?start=${dossierStart}&end=${dossierEnd}&top_per_type=30`
			);
			if (!r.ok) return;
			dossier = await r.json();
		} catch {
			dossier = null;
		}
	}

	async function load() {
		if (!ticker) return;
		loading = true;
		error = null;
		try {
			const today = new Date().toISOString().slice(0, 10);
			// ingest_years=2 on first load — covers ~8 quarterly DERA releases so both
			// calendar-year and fiscal-year filers have a recent 10-K somewhere in the window.
			// First-ever call backfills several quarters (~10-40s); subsequent are instant.
			const [fRes, pRes, rRes] = await Promise.all([
				fetch(`/api/fundamentals/${encodeURIComponent(ticker)}?years=${years}&ingest_years=2`),
				fetch(`/api/prices/${encodeURIComponent(ticker)}?start=2020-01-01&end=${today}`),
				fetch(`/api/fundamentals/${encodeURIComponent(ticker)}/ratios`)
			]);
			if (!fRes.ok) throw new Error(`fundamentals ${fRes.status}`);
			data = await fRes.json();
			if (pRes.ok) {
				const pJson = await pRes.json();
				info = pJson.info ?? null;
			}
			ratios = rRes.ok ? await rRes.json() : null;
			// Kick off dossier in parallel — doesn't block initial render
			loadDossier();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			data = null;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		ticker;
		years;
		load();
	});

	const fmt = (n: number, uom: string) => {
		if (uom === 'shares') {
			if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
			if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
			return n.toLocaleString();
		}
		if (uom === 'USD/shares') return `$${n.toFixed(2)}`;
		// USD
		const abs = Math.abs(n);
		if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
		if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
		if (abs >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
		return `$${n.toFixed(0)}`;
	};

	// Collect distinct fiscal years across all statements (descending)
	const fiscalYears = $derived.by(() => {
		if (!data) return [] as number[];
		const s = new Set<number>();
		for (const stmt of ['IS', 'BS', 'CF'] as const) {
			const items = data.statements?.[stmt] ?? {};
			for (const tag in items) {
				for (const r of items[tag].rows) {
					if (r.fy != null) s.add(r.fy);
				}
			}
		}
		return Array.from(s).sort((a, b) => b - a);
	});

	// Pick one representative value per (tag, fy): the max (consolidated tends to exceed duplicates)
	function pickValue(rows: Row[], fy: number): number | null {
		const match = rows.filter((r) => r.fy === fy);
		if (!match.length) return null;
		// Pick largest-magnitude value — matches the consolidated total in most cases.
		return match.reduce((a, b) => (Math.abs(b.value) > Math.abs(a.value) ? b : a)).value;
	}

	// Keep rows with at least one non-null value across years
	function visibleTags(items: Record<string, Item>): string[] {
		return Object.keys(items).filter((tag) => {
			return fiscalYears.some((y) => pickValue(items[tag].rows, y) != null);
		});
	}
</script>

<h1>{ticker}{data?.name ? ` — ${data.name}` : ''}</h1>

{#if info}
	<div class="meta">
		{#if info.sector}<span class="tag sector">{info.sector}</span>{/if}
		{#if info.industry}<span class="tag industry">{info.industry}</span>{/if}
		{#if info.exchange}<span class="tag">{info.exchange}</span>{/if}
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

<section class="controls">
	<div class="search-slot"><TickerSearch placeholder="Jump to another ticker…" /></div>
	<label>
		<span>Years</span>
		<input type="number" min="1" max="15" bind:value={years} />
	</label>
	<a href="/insider-buying" class="nav-link">← insider buying</a>
</section>

<section class="tabs">
	<button class:active={tab === 'fundamentals'} onclick={() => (tab = 'fundamentals')}>
		📊 Fundamentals
	</button>
	<button class:active={tab === 'activity'} onclick={() => (tab = 'activity')}>
		📋 SEC Activity
		{#if dossier}
			<span class="badge">{Object.values(dossier.summary).reduce((a, b) => a + b, 0)}</span>
		{/if}
	</button>
</section>

{#if loading}
	<div class="status">
		Loading fundamentals for {ticker}… <span class="dim">(first-ever load may backfill ~8 DERA quarters, can take 30-60s)</span>
	</div>
{:else if tab === 'fundamentals' && ratios?.ratios}
	{@const r = ratios.ratios}
	{@const fmtPct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)}
	{@const fmtX = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)}x`)}
	{@const fmtMoneyS = (v: number | null) => {
		if (v == null) return '—';
		const a = Math.abs(v);
		if (a >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
		if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
		if (a >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
		return `${v.toFixed(0)}`;
	}}
	<section class="panel ratios-panel">
		<h2>
			Key ratios
			<span class="panel-sub">
				— derived from {ratios.period} 10-K
				{#if ratios.prior_period}· YoY vs {ratios.prior_period}{/if}
			</span>
		</h2>
		<div class="ratios-grid">
			<div class="ratio-group">
				<h3>Valuation</h3>
				<div class="ratio"><span>Price</span><strong>${r.price?.toFixed(2) ?? '—'}</strong></div>
				<div class="ratio"><span>Market cap</span><strong>${fmtMoneyS(r.market_cap)}</strong></div>
				<div class="ratio"><span>EV</span><strong>${fmtMoneyS(r.enterprise_value)}</strong></div>
				<div class="ratio"><span>P/E</span><strong>{fmtX(r.pe_ratio)}</strong></div>
				<div class="ratio"><span>P/B</span><strong>{fmtX(r.pb_ratio)}</strong></div>
				<div class="ratio"><span>P/S</span><strong>{fmtX(r.ps_ratio)}</strong></div>
				<div class="ratio"><span>EV/EBITDA</span><strong>{fmtX(r.ev_ebitda)}</strong></div>
				<div class="ratio"><span>EV/Revenue</span><strong>{fmtX(r.ev_revenue)}</strong></div>
				<div class="ratio"><span>FCF yield</span><strong>{fmtPct(r.fcf_yield)}</strong></div>
				<div class="ratio"><span>Earnings yield</span><strong>{fmtPct(r.earnings_yield)}</strong></div>
			</div>
			<div class="ratio-group">
				<h3>Profitability</h3>
				<div class="ratio"><span>Gross margin</span><strong>{fmtPct(r.gross_margin)}</strong></div>
				<div class="ratio"><span>Operating margin</span><strong>{fmtPct(r.operating_margin)}</strong></div>
				<div class="ratio"><span>Net margin</span><strong>{fmtPct(r.net_margin)}</strong></div>
				<div class="ratio"><span>ROE</span><strong>{fmtPct(r.roe)}</strong></div>
				<div class="ratio"><span>ROA</span><strong>{fmtPct(r.roa)}</strong></div>
			</div>
			<div class="ratio-group">
				<h3>Financial health</h3>
				<div class="ratio"><span>Debt/Equity</span><strong>{fmtX(r.debt_to_equity)}</strong></div>
				<div class="ratio"><span>Debt/EBITDA</span><strong>{fmtX(r.debt_to_ebitda)}</strong></div>
				<div class="ratio"><span>Net debt</span><strong>${fmtMoneyS(r.net_debt)}</strong></div>
			</div>
			<div class="ratio-group">
				<h3>Growth (YoY)</h3>
				<div class="ratio">
					<span>Revenue</span>
					<strong class:up={(r.revenue_growth_yoy ?? 0) >= 0} class:down={(r.revenue_growth_yoy ?? 0) < 0}>
						{r.revenue_growth_yoy != null && r.revenue_growth_yoy >= 0 ? '+' : ''}{fmtPct(r.revenue_growth_yoy)}
					</strong>
				</div>
				<div class="ratio">
					<span>Net income</span>
					<strong class:up={(r.net_income_growth_yoy ?? 0) >= 0} class:down={(r.net_income_growth_yoy ?? 0) < 0}>
						{r.net_income_growth_yoy != null && r.net_income_growth_yoy >= 0 ? '+' : ''}{fmtPct(r.net_income_growth_yoy)}
					</strong>
				</div>
			</div>
		</div>
	</section>
{/if}

{#if loading}
	<!-- placeholder already shown above -->
{:else if error || data?.error}
	<div class="error">Error: {error ?? data?.error}</div>
{:else if tab === 'fundamentals' && data}
	{#each [['IS', 'Income Statement'], ['BS', 'Balance Sheet'], ['CF', 'Cash Flow']] as [key, title]}
		{@const items = data.statements[key as keyof Statements] ?? {}}
		{@const tags = visibleTags(items)}
		<section class="panel">
			<h2>{title}</h2>
			{#if !tags.length}
				<p class="empty">No {key} line items in window.</p>
			{:else}
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th>Line item</th>
								{#each fiscalYears as y}
									<th class="num">FY{String(y).slice(-2)}</th>
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each tags as tag}
								{@const item = items[tag]}
								<tr>
									<td class="label" title={tag}>{item.label}</td>
									{#each fiscalYears as y}
										{@const v = pickValue(item.rows, y)}
										<td class="num">{v != null ? fmt(v, item.uom) : '—'}</td>
									{/each}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/each}
{/if}

{#if tab === 'activity'}
	{#if !dossier}
		<div class="status">Loading SEC activity for {ticker}…</div>
	{:else}
		{@const s = dossier.summary}
		{@const fmtM = (v: number) => {
			const a = Math.abs(v);
			if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
			if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
			if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
			return `$${v.toFixed(0)}`;
		}}

		<section class="controls activity-controls">
			<label><span>Start</span><input type="date" bind:value={dossierStart} /></label>
			<label><span>End</span><input type="date" bind:value={dossierEnd} /></label>
			<button onclick={loadDossier}>Reload</button>
		</section>

		<section class="activity-kpis">
			<div class="a-kpi" class:bull={s.insider_buys > 0}>
				<span class="a-label">Insider buys</span>
				<span class="a-value">{s.insider_buys}</span>
				<span class="a-sub">{fmtM((s.insider_buys_total as number) ?? 0)}</span>
			</div>
			<div class="a-kpi" class:bear={s.planned_sells > 0}>
				<span class="a-label">Planned sells (144)</span>
				<span class="a-value">{s.planned_sells}</span>
				<span class="a-sub">{fmtM((s.planned_sells_total as number) ?? 0)}</span>
			</div>
			<div class="a-kpi"><span class="a-label">13D filings</span><span class="a-value">{s.filings_13d}</span></div>
			<div class="a-kpi"><span class="a-label">13G filings</span><span class="a-value">{s.filings_13g}</span></div>
			<div class="a-kpi"><span class="a-label">8-K filings</span><span class="a-value">{s.eight_k}</span></div>
			<div class="a-kpi" class:bear={s.late_filings > 0}>
				<span class="a-label">Late filings</span><span class="a-value">{s.late_filings}</span>
			</div>
			<div class="a-kpi" class:warn={s.proxy_contests > 0}>
				<span class="a-label">Proxy contests</span><span class="a-value">{s.proxy_contests}</span>
			</div>
			<div class="a-kpi" class:bear={s.secondaries > 0}>
				<span class="a-label">Secondaries</span><span class="a-value">{s.secondaries}</span>
			</div>
			<div class="a-kpi" class:bull={s.tender_offers > 0}>
				<span class="a-label">Tender offers</span><span class="a-value">{s.tender_offers}</span>
			</div>
			<div class="a-kpi"><span class="a-label">New insiders (F3)</span><span class="a-value">{s.form3}</span></div>
			<div class="a-kpi" class:bull={s.superinvestor_holders > 0}>
				<span class="a-label">Superinv holders</span><span class="a-value">{s.superinvestor_holders}</span>
			</div>
		</section>

		{#if dossier.superinvestor_holders.length}
			<section class="panel">
				<h2>🏛 Superinvestors holding {ticker}</h2>
				<div class="table-wrap">
					<table>
						<thead><tr><th>Fund</th><th class="num">Value</th><th class="num">Shares</th><th>As of</th></tr></thead>
						<tbody>
							{#each dossier.superinvestor_holders as h}
								<tr>
									<td><strong>{h.fund_label}</strong> <span class="dim">{h.fund_name}</span></td>
									<td class="num">{fmtM(h.value_usd)}</td>
									<td class="num">{h.shares.toLocaleString()}</td>
									<td>{h.period_end}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		{#if dossier.insider_buys.length}
			<section class="panel">
				<h2>🟢 Insider buys (Form 4)</h2>
				<div class="table-wrap">
					<table>
						<thead><tr><th>Date</th><th>Insider</th><th>Position</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th></tr></thead>
						<tbody>
							{#each dossier.insider_buys as b}
								<tr>
									<td>{b.date}</td>
									<td>{b.insider_name}</td>
									<td class="dim">{b.position || '—'}</td>
									<td class="num">{b.shares.toLocaleString()}</td>
									<td class="num">${b.price.toFixed(2)}</td>
									<td class="num up">{fmtM(b.value)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		{#if dossier.planned_sells.length}
			<section class="panel">
				<h2>🔴 Planned sells (Form 144)</h2>
				<div class="table-wrap">
					<table>
						<thead><tr><th>Filed</th><th>Approx sale</th><th>Seller</th><th>Role</th><th class="num">Shares</th><th class="num">$ value</th></tr></thead>
						<tbody>
							{#each dossier.planned_sells as p}
								<tr>
									<td>{p.filing_date}</td>
									<td class="dim">{p.approx_sale_date ?? '—'}</td>
									<td>{p.seller_name}</td>
									<td class="dim">{p.relationship}</td>
									<td class="num">{p.shares.toLocaleString()}</td>
									<td class="num down">{fmtM(p.aggregate_value)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		{#if dossier.thirteen_d.length || dossier.thirteen_g.length}
			<section class="panel">
				<h2>🎯 Activist + passive 5%+ (13D / 13G)</h2>
				<div class="table-wrap">
					<table>
						<thead><tr><th>Date</th><th>Form</th><th>Filer</th></tr></thead>
						<tbody>
							{#each [...dossier.thirteen_d, ...dossier.thirteen_g].sort((a, b) => b.filing_date.localeCompare(a.filing_date)) as f}
								<tr>
									<td>{f.filing_date}</td>
									<td><span class="form-tag">{f.form}</span></td>
									<td>{f.filer_name}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		{#if dossier.eight_k.length}
			<section class="panel">
				<h2>📰 Material events (8-K)</h2>
				<div class="table-wrap">
					<table>
						<thead><tr><th>Date</th><th>Items</th></tr></thead>
						<tbody>
							{#each dossier.eight_k as e}
								<tr>
									<td>{e.filing_date}</td>
									<td>
										{#each e.items as i}<span class="form-tag">{i}</span>{/each}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}

		{#if dossier.proxy_contests.length || dossier.tender_offers.length || dossier.secondaries.length || dossier.late_filings.length || dossier.form3.length}
			<section class="panel">
				<h2>Other filings</h2>
				<div class="misc-grid">
					{#if dossier.proxy_contests.length}
						<div><h3>Proxy contests</h3>
							<ul>{#each dossier.proxy_contests as f}<li>{f.filing_date} · <span class="form-tag">{f.form}</span> · {f.filer_name}</li>{/each}</ul></div>
					{/if}
					{#if dossier.tender_offers.length}
						<div><h3>Tender offers</h3>
							<ul>{#each dossier.tender_offers as f}<li>{f.filing_date} · <span class="form-tag">{f.form}</span> · {f.acquirer}</li>{/each}</ul></div>
					{/if}
					{#if dossier.secondaries.length}
						<div><h3>Secondaries</h3>
							<ul>{#each dossier.secondaries as f}<li>{f.filing_date} · <span class="form-tag">{f.form}</span></li>{/each}</ul></div>
					{/if}
					{#if dossier.late_filings.length}
						<div><h3>Late filings</h3>
							<ul>{#each dossier.late_filings as f}<li>{f.filing_date} · <span class="form-tag">{f.form}</span></li>{/each}</ul></div>
					{/if}
					{#if dossier.form3.length}
						<div><h3>New insiders (Form 3)</h3>
							<ul>{#each dossier.form3 as f}<li>{f.filing_date} · {f.filer_name}</li>{/each}</ul></div>
					{/if}
				</div>
			</section>
		{/if}
	{/if}
{/if}

<style>
	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.75rem;
	}
	.meta {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
		margin-bottom: 1rem;
	}
	.tag {
		background: #1e293b;
		border: 1px solid #334155;
		color: #94a3b8;
		font-size: 0.75rem;
		padding: 0.2rem 0.55rem;
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
	.controls {
		display: flex;
		align-items: center;
		gap: 1.5rem;
		padding: 0.75rem 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin-bottom: 1rem;
		flex-wrap: wrap;
	}
	.search-slot {
		display: flex;
		flex: 1;
		min-width: 260px;
	}
	.controls label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	.controls input {
		background: #0f172a;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.3rem 0.5rem;
		border-radius: 4px;
		width: 60px;
	}
	.nav-link {
		color: #60a5fa;
		text-decoration: none;
		font-size: 0.85rem;
	}
	.nav-link:hover {
		text-decoration: underline;
	}
	.panel {
		background: #1e293b;
		padding: 1rem 1.25rem;
		border-radius: 8px;
		margin-bottom: 1rem;
	}
	.panel h2 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.table-wrap {
		overflow-x: auto;
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
	.label {
		color: #cbd5e1;
		cursor: help;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
	}
	.dim {
		color: #64748b;
		font-size: 0.85em;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
	}
	.empty {
		color: #94a3b8;
	}
	.tabs {
		display: flex;
		gap: 0.25rem;
		margin: 1rem 0;
		border-bottom: 1px solid #334155;
	}
	.tabs button {
		background: transparent;
		border: none;
		color: #94a3b8;
		padding: 0.6rem 1rem;
		cursor: pointer;
		font-size: 0.95rem;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
	}
	.tabs button:hover { color: #e2e8f0; }
	.tabs button.active {
		color: #60a5fa;
		border-bottom-color: #60a5fa;
	}
	.tabs button .badge {
		background: #334155;
		color: #e2e8f0;
		font-size: 0.7rem;
		padding: 0.1rem 0.4rem;
		border-radius: 999px;
		font-variant-numeric: tabular-nums;
	}
	.activity-controls { margin-top: 0; }
	.activity-kpis {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.a-kpi {
		background: #1e293b;
		padding: 0.6rem 0.75rem;
		border-radius: 6px;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		border: 1px solid transparent;
	}
	.a-kpi.bull { border-color: #166534; background: #14532d40; }
	.a-kpi.bear { border-color: #7f1d1d; background: #7f1d1d40; }
	.a-kpi.warn { border-color: #78350f; background: #78350f40; }
	.a-label {
		color: #94a3b8;
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.a-value {
		font-size: 1.25rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.a-sub {
		color: #94a3b8;
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
	}
	.form-tag {
		display: inline-block;
		background: #334155;
		color: #cbd5e1;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
		font-size: 0.7rem;
		margin-right: 0.25rem;
		text-transform: uppercase;
		font-variant-numeric: tabular-nums;
	}
	.dim { color: #94a3b8; }
	.up { color: #4ade80; }
	.down { color: #f87171; }
	.misc-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
		gap: 1rem;
	}
	.misc-grid h3 {
		margin: 0 0 0.5rem;
		font-size: 0.85rem;
		color: #94a3b8;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.misc-grid ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.misc-grid li {
		padding: 0.3rem 0;
		border-bottom: 1px solid #334155;
		font-size: 0.85rem;
	}
	.ratios-panel .panel-sub {
		color: #64748b;
		font-size: 0.8rem;
		font-weight: normal;
	}
	.ratios-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 1rem;
	}
	.ratio-group h3 {
		margin: 0 0 0.5rem;
		font-size: 0.75rem;
		color: #94a3b8;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
	}
	.ratio {
		display: flex;
		justify-content: space-between;
		padding: 0.3rem 0;
		border-bottom: 1px solid #334155;
		font-size: 0.85rem;
	}
	.ratio:last-child {
		border-bottom: none;
	}
	.ratio span {
		color: #94a3b8;
	}
	.ratio strong {
		color: #e2e8f0;
		font-variant-numeric: tabular-nums;
	}
	.ratio strong.up {
		color: #4ade80;
	}
	.ratio strong.down {
		color: #f87171;
	}
</style>

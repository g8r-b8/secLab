<script lang="ts">
	type Row = {
		ticker: string;
		cik: number;
		name: string;
		period: string;
		price: number | null;
		market_cap: number | null;
		pe_ratio: number | null;
		pb_ratio: number | null;
		ps_ratio: number | null;
		ev_ebitda: number | null;
		fcf_yield: number | null;
		earnings_yield: number | null;
		gross_margin: number | null;
		operating_margin: number | null;
		net_margin: number | null;
		roe: number | null;
		roa: number | null;
		debt_to_equity: number | null;
		revenue_growth_yoy: number | null;
		net_income_growth_yoy: number | null;
		quality_score: number | null;
		magic_rank: number | null;
	};
	type Response = {
		rows: Row[];
		columns: string[];
		total: number;
		computed_at: string | null;
	};

	type NumOrEmpty = number | '';

	let pe_max = $state<NumOrEmpty>('');
	let pb_max = $state<NumOrEmpty>('');
	let fcf_yield_min = $state<NumOrEmpty>(''); // percent
	let roe_min = $state<NumOrEmpty>(''); // percent
	let net_margin_min = $state<NumOrEmpty>(''); // percent
	let revenue_growth_min = $state<NumOrEmpty>(''); // percent
	let debt_to_equity_max = $state<NumOrEmpty>('');
	let market_cap_min = $state<NumOrEmpty>(''); // in millions
	let market_cap_max = $state<NumOrEmpty>('');
	let quality_score_min = $state<NumOrEmpty>(''); // 0-5
	let magic_rank_max = $state<NumOrEmpty>(''); // lower = better
	let exclude_etfs = $state(true);

	let orderBy = $state('market_cap');
	let descending = $state(true);
	let limit = $state(100);

	let data = $state<Response | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let refreshing = $state(false);
	let refreshStatus = $state<string | null>(null);

	function toPct(v: NumOrEmpty): number | undefined {
		return v === '' ? undefined : Number(v) / 100;
	}

	async function run() {
		loading = true;
		error = null;
		try {
			const params = new URLSearchParams({
				order_by: orderBy,
				descending: String(descending),
				limit: String(limit)
			});
			const push = (k: string, v: number | undefined) => {
				if (v != null && !Number.isNaN(v)) params.set(k, String(v));
			};
			push('pe_ratio_max', pe_max === '' ? undefined : Number(pe_max));
			push('pb_ratio_max', pb_max === '' ? undefined : Number(pb_max));
			push('fcf_yield_min', toPct(fcf_yield_min));
			push('roe_min', toPct(roe_min));
			push('net_margin_min', toPct(net_margin_min));
			push('revenue_growth_yoy_min', toPct(revenue_growth_min));
			push('debt_to_equity_max', debt_to_equity_max === '' ? undefined : Number(debt_to_equity_max));
			push('market_cap_min', market_cap_min === '' ? undefined : Number(market_cap_min) * 1e6);
			push('market_cap_max', market_cap_max === '' ? undefined : Number(market_cap_max) * 1e6);
			push('quality_score_min', quality_score_min === '' ? undefined : Number(quality_score_min));
			push('magic_rank_max', magic_rank_max === '' ? undefined : Number(magic_rank_max));
			if (exclude_etfs) params.set('exclude_etfs', 'true');
			const r = await fetch(`/api/screener?${params}`);
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
		orderBy;
		descending;
		limit;
		run();
	});

	async function refreshSnapshot() {
		refreshing = true;
		refreshStatus = 'Computing ratios snapshot across the universe — can take 10-30 minutes…';
		try {
			const r = await fetch('/api/screener/refresh', { method: 'POST' });
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const j = await r.json();
			refreshStatus =
				`Done. ${j.stored ?? 0} tickers stored, prices ${j.price_ok ?? 0} ok / ${j.price_missing ?? 0} missing.`;
			run();
		} catch (e) {
			refreshStatus = `Refresh failed: ${e instanceof Error ? e.message : e}`;
		} finally {
			refreshing = false;
		}
	}

	const fmtPct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
	const fmtX = (v: number | null) => (v == null ? '—' : v.toFixed(1) + 'x');
	const fmtMoneyS = (v: number | null) => {
		if (v == null) return '—';
		const a = Math.abs(v);
		if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
		if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
		if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
		return `$${v.toFixed(0)}`;
	};
</script>

<h1>🔍 Screener</h1>
<p class="subtitle">
	Cross-sectional ratios across every US-listed ticker with a recent 10-K. Compustat-lite.
	Filter by valuation, profitability, growth, leverage — then click a ticker to dive into its
	full fundamentals page.
</p>

<section class="snapshot">
	{#if data}
		<span class="meta">
			{data.total.toLocaleString()} tickers in snapshot
			{#if data.computed_at}· last computed {data.computed_at.slice(0, 16)}{/if}
		</span>
	{/if}
	<button class="refresh" onclick={refreshSnapshot} disabled={refreshing}>
		{refreshing ? 'Refreshing…' : 'Refresh snapshot'}
	</button>
	{#if refreshStatus}
		<span class="status">{refreshStatus}</span>
	{/if}
</section>

<section class="filters">
	<label>
		<span>Market cap ($M) ≥</span>
		<input type="number" min="0" bind:value={market_cap_min} />
	</label>
	<label>
		<span>Market cap ($M) ≤</span>
		<input type="number" min="0" bind:value={market_cap_max} />
	</label>
	<label>
		<span>P/E ≤</span>
		<input type="number" min="0" step="1" bind:value={pe_max} />
	</label>
	<label>
		<span>P/B ≤</span>
		<input type="number" min="0" step="1" bind:value={pb_max} />
	</label>
	<label>
		<span>FCF yield (%) ≥</span>
		<input type="number" step="0.5" bind:value={fcf_yield_min} />
	</label>
	<label>
		<span>ROE (%) ≥</span>
		<input type="number" step="1" bind:value={roe_min} />
	</label>
	<label>
		<span>Net margin (%) ≥</span>
		<input type="number" step="1" bind:value={net_margin_min} />
	</label>
	<label>
		<span>Rev growth (%) ≥</span>
		<input type="number" step="1" bind:value={revenue_growth_min} />
	</label>
	<label>
		<span>D/E ≤</span>
		<input type="number" step="0.1" bind:value={debt_to_equity_max} />
	</label>
	<label title="Reduced Piotroski F: 1 pt each for NI>0, ROA>0, rev growth>0, NI growth>0, D/E<1. Max = 5.">
		<span>Quality (0-5) ≥</span>
		<input type="number" min="0" max="5" step="1" bind:value={quality_score_min} />
	</label>
	<label title="Magic Formula approx: sum of ranks on earnings yield + ROA. Lower = cheaper × higher-quality.">
		<span>Magic rank ≤</span>
		<input type="number" min="1" step="50" bind:value={magic_rank_max} />
	</label>
	<label class="checkbox" title="Drop ETFs / trusts / funds whose accounting distorts quality/value ranks.">
		<input type="checkbox" bind:checked={exclude_etfs} />
		<span>Exclude ETFs</span>
	</label>
	<label>
		<span>Top N</span>
		<input type="number" min="10" max="1000" step="50" bind:value={limit} />
	</label>
	<button class="run" onclick={run} disabled={loading}>{loading ? 'Loading…' : 'Apply'}</button>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}

{#if loading}
	<div class="status">Querying snapshot…</div>
{:else if data && data.rows.length === 0}
	<div class="warning">
		No rows match these filters. {#if data.total === 0}
			Snapshot is empty — click <strong>Refresh snapshot</strong> above to compute ratios across
			the universe. First run can take 10-30 minutes depending on rate limits.
		{:else}
			Loosen filters or widen the market cap range.
		{/if}
	</div>
{:else if data}
	<section class="panel">
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th
							class="sortable"
							onclick={() => {
								if (orderBy === 'ticker') descending = !descending;
								orderBy = 'ticker';
							}}
						>
							Ticker {orderBy === 'ticker' ? (descending ? '↓' : '↑') : ''}
						</th>
						<th>Name</th>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'market_cap') descending = !descending;
								else orderBy = 'market_cap';
							}}
						>
							Mkt Cap {orderBy === 'market_cap' ? (descending ? '↓' : '↑') : ''}
						</th>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'pe_ratio') descending = !descending;
								else orderBy = 'pe_ratio';
							}}>P/E</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'pb_ratio') descending = !descending;
								else orderBy = 'pb_ratio';
							}}>P/B</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'fcf_yield') descending = !descending;
								else orderBy = 'fcf_yield';
							}}>FCF Y</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'roe') descending = !descending;
								else orderBy = 'roe';
							}}>ROE</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'net_margin') descending = !descending;
								else orderBy = 'net_margin';
							}}>Net mgn</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'revenue_growth_yoy') descending = !descending;
								else orderBy = 'revenue_growth_yoy';
							}}>Rev Δ</th
						>
						<th
							class="num sortable"
							onclick={() => {
								if (orderBy === 'debt_to_equity') descending = !descending;
								else orderBy = 'debt_to_equity';
							}}>D/E</th
						>
						<th
							class="num sortable"
							title="Reduced Piotroski F-score (0-5): NI>0, ROA>0, rev growth, NI growth, D/E<1"
							onclick={() => {
								if (orderBy === 'quality_score') descending = !descending;
								else {
									orderBy = 'quality_score';
									descending = true;
								}
							}}>Q {orderBy === 'quality_score' ? (descending ? '↓' : '↑') : ''}</th
						>
						<th
							class="num sortable"
							title="Magic Formula rank approx: sum of ranks on earnings yield + ROA. Lower = cheaper × higher-quality."
							onclick={() => {
								if (orderBy === 'magic_rank') descending = !descending;
								else {
									orderBy = 'magic_rank';
									descending = false;
								}
							}}>MF {orderBy === 'magic_rank' ? (descending ? '↓' : '↑') : ''}</th
						>
					</tr>
				</thead>
				<tbody>
					{#each data.rows as r}
						<tr>
							<td class="ticker">
								<a href="/fundamentals/{r.ticker}">{r.ticker}</a>
							</td>
							<td class="name">{r.name}</td>
							<td class="num">{fmtMoneyS(r.market_cap)}</td>
							<td class="num">{fmtX(r.pe_ratio)}</td>
							<td class="num">{fmtX(r.pb_ratio)}</td>
							<td class="num">{fmtPct(r.fcf_yield)}</td>
							<td class="num" class:up={(r.roe ?? 0) > 0}>{fmtPct(r.roe)}</td>
							<td class="num" class:up={(r.net_margin ?? 0) > 0} class:down={(r.net_margin ?? 0) < 0}>
								{fmtPct(r.net_margin)}
							</td>
							<td
								class="num"
								class:up={(r.revenue_growth_yoy ?? 0) > 0}
								class:down={(r.revenue_growth_yoy ?? 0) < 0}
							>
								{fmtPct(r.revenue_growth_yoy)}
							</td>
							<td class="num">{fmtX(r.debt_to_equity)}</td>
							<td
								class="num quality"
								class:q-high={(r.quality_score ?? 0) >= 4}
								class:q-low={(r.quality_score ?? 0) <= 1}
							>
								{r.quality_score ?? '—'}
							</td>
							<td class="num">{r.magic_rank ?? '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
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
	.snapshot {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1rem 0;
		font-size: 0.85rem;
		color: #94a3b8;
		flex-wrap: wrap;
	}
	.refresh {
		background: transparent;
		border: 1px solid #334155;
		color: #cbd5e1;
		padding: 0.3rem 0.8rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.8rem;
	}
	.refresh:hover {
		border-color: #60a5fa;
		color: #e2e8f0;
	}
	.filters {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 0.75rem;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin-bottom: 1rem;
		align-items: end;
	}
	.filters label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.75rem;
		color: #94a3b8;
	}
	.filters input {
		background: #0f172a;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	.run {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.55rem 1rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.9rem;
	}
	.panel {
		background: #1e293b;
		padding: 1rem;
		border-radius: 8px;
	}
	.table-wrap {
		overflow: auto;
		max-height: 720px;
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
	th.sortable {
		cursor: pointer;
		user-select: none;
	}
	th.sortable:hover {
		color: #e2e8f0;
	}
	th.num,
	td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.ticker a {
		color: #60a5fa;
		font-weight: 600;
		text-decoration: none;
	}
	.ticker a:hover {
		text-decoration: underline;
	}
	.name {
		color: #cbd5e1;
		max-width: 280px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	.quality {
		font-weight: 600;
	}
	.q-high {
		color: #22c55e;
	}
	.q-low {
		color: #f87171;
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
		padding: 0.75rem 1rem;
		border-radius: 4px;
		margin-bottom: 0.5rem;
	}
</style>

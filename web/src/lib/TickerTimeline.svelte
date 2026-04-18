<script lang="ts">
	type Props = { ticker: string; start: string; end: string };
	let { ticker, start, end }: Props = $props();

	type Buy = {
		kind: 'buy';
		date: string;
		issuer: string;
		shares: number;
		price: number;
		value: number;
		accession: string;
		insider_name: string;
		position: string;
	};

	type Sell = {
		kind: 'sell';
		date: string;
		filing_date: string;
		approx_sale_date: string | null;
		seller_name: string;
		relationship: string;
		shares: number;
		aggregate_value: number;
		implied_price: number;
	};

	type Event = Buy | Sell;

	let events = $state<Event[]>([]);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load() {
		if (!ticker) return;
		loading = true;
		error = null;
		try {
			const [bRes, sRes] = await Promise.all([
				fetch(
					`/api/tickers/${encodeURIComponent(ticker)}/insider-buys?start=${start}&end=${end}`
				),
				fetch(`/api/tickers/${encodeURIComponent(ticker)}/form144?start=${start}&end=${end}`)
			]);
			if (!bRes.ok) throw new Error(`buys ${bRes.status}`);
			const bJson = await bRes.json();
			const sJson = sRes.ok ? await sRes.json() : { sells: [] };
			const buys: Buy[] = (bJson.buys ?? []).map((b: Omit<Buy, 'kind'>) => ({
				...b,
				kind: 'buy' as const
			}));
			const sells: Sell[] = (sJson.sells ?? []).map(
				(
					s: {
						filing_date: string;
						approx_sale_date: string | null;
						seller_name: string;
						relationship: string;
						shares: number;
						aggregate_value: number;
					}
				) => ({
					kind: 'sell' as const,
					date: s.filing_date,
					filing_date: s.filing_date,
					approx_sale_date: s.approx_sale_date,
					seller_name: s.seller_name,
					relationship: s.relationship,
					shares: s.shares,
					aggregate_value: s.aggregate_value,
					implied_price: s.shares > 0 ? s.aggregate_value / s.shares : 0
				})
			);
			events = [...buys, ...sells].sort((a, b) => a.date.localeCompare(b.date));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			events = [];
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		ticker;
		start;
		end;
		load();
	});

	const fmt = (n: number) =>
		n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}K` : `$${n.toFixed(0)}`;

	// Group by date so clustered same-day events stack under one timeline node
	type DayGroup = { date: string; rows: Event[]; buyTotal: number; sellTotal: number };
	const groups = $derived.by<DayGroup[]>(() => {
		const by: Record<string, Event[]> = {};
		for (const e of events) (by[e.date] ??= []).push(e);
		return Object.entries(by)
			.map(([date, rows]) => ({
				date,
				rows,
				buyTotal: rows.reduce((s, r) => s + (r.kind === 'buy' ? r.value : 0), 0),
				sellTotal: rows.reduce((s, r) => s + (r.kind === 'sell' ? r.aggregate_value : 0), 0)
			}))
			.sort((a, b) => a.date.localeCompare(b.date));
	});
</script>

<div class="wrap">
	<h3>Purchase timeline</h3>
	{#if loading}
		<div class="status">Loading…</div>
	{:else if error}
		<div class="error">{error}</div>
	{:else if !events.length}
		<div class="status">No insider activity for {ticker} in this window.</div>
	{:else}
		<ol class="timeline">
			{#each groups as g}
				{@const kinds = new Set(g.rows.map((r) => r.kind))}
				{@const dominantKind = kinds.has('buy') && kinds.has('sell')
					? 'mixed'
					: kinds.has('buy')
						? 'buy'
						: 'sell'}
				<li>
					<div class="marker" class:sell={dominantKind === 'sell'} class:mixed={dominantKind === 'mixed'}></div>
					<div class="content">
						<header>
							<span class="date">{g.date}</span>
							{#if g.buyTotal > 0}
								<span class="buy-total">+{fmt(g.buyTotal)} buy</span>
							{/if}
							{#if g.sellTotal > 0}
								<span class="sell-total">−{fmt(g.sellTotal)} planned sell</span>
							{/if}
							<span class="count">{g.rows.length} event{g.rows.length === 1 ? '' : 's'}</span>
						</header>
						<ul class="events">
							{#each g.rows as r}
								{#if r.kind === 'buy'}
									<li class="ev-buy">
										<span class="kind-badge buy">BUY</span>
										<span class="name">{r.insider_name}</span>
										<span class="pos">{r.position || '—'}</span>
										<span class="detail">
											{r.shares.toLocaleString()} sh @ ${r.price.toFixed(2)}
											<strong>= {fmt(r.value)}</strong>
										</span>
									</li>
								{:else}
									<li class="ev-sell">
										<span class="kind-badge sell">PLAN SELL</span>
										<span class="name">{r.seller_name}</span>
										<span class="pos">{r.relationship || '—'}</span>
										<span class="detail">
											{r.shares.toLocaleString()} sh · implied ${r.implied_price.toFixed(2)}
											<strong class="sell-strong">= {fmt(r.aggregate_value)}</strong>
											{#if r.approx_sale_date}
												<span class="approx">approx {r.approx_sale_date}</span>
											{/if}
										</span>
									</li>
								{/if}
							{/each}
						</ul>
					</div>
				</li>
			{/each}
		</ol>
	{/if}
</div>

<style>
	.wrap {
		margin-top: 1rem;
	}
	h3 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
		color: #e2e8f0;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
	}
	.error {
		color: #fecaca;
		background: #7f1d1d;
		padding: 0.5rem;
		border-radius: 4px;
	}
	.timeline {
		list-style: none;
		padding: 0;
		margin: 0;
		position: relative;
	}
	.timeline::before {
		content: '';
		position: absolute;
		left: 7px;
		top: 0;
		bottom: 0;
		width: 2px;
		background: #334155;
	}
	.timeline > li {
		position: relative;
		padding-left: 2rem;
		padding-bottom: 1.25rem;
	}
	.marker {
		position: absolute;
		left: 0;
		top: 0.35rem;
		width: 16px;
		height: 16px;
		background: #22c55e;
		border: 2px solid #0f172a;
		border-radius: 50%;
	}
	.marker.sell {
		background: #ef4444;
	}
	.marker.mixed {
		background: linear-gradient(135deg, #22c55e 0%, #22c55e 50%, #ef4444 50%, #ef4444 100%);
	}
	.content header {
		display: flex;
		gap: 0.75rem;
		align-items: baseline;
		margin-bottom: 0.4rem;
	}
	.date {
		font-weight: 600;
		color: #e2e8f0;
		font-variant-numeric: tabular-nums;
	}
	.buy-total {
		color: #4ade80;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}
	.sell-total {
		color: #f87171;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}
	.count {
		color: #64748b;
		font-size: 0.8rem;
	}
	.events {
		list-style: none;
		padding: 0;
		margin: 0;
		font-size: 0.85rem;
	}
	.events > li {
		display: grid;
		grid-template-columns: 4.5rem 1.5fr 1fr 1.5fr;
		gap: 0.6rem;
		padding: 0.35rem 0;
		border-bottom: 1px solid #1e293b;
		color: #cbd5e1;
		align-items: baseline;
	}
	.kind-badge {
		font-size: 0.65rem;
		padding: 0.1rem 0.35rem;
		border-radius: 3px;
		letter-spacing: 0.04em;
		text-align: center;
		font-weight: 600;
	}
	.kind-badge.buy {
		background: #166534;
		color: #bbf7d0;
	}
	.kind-badge.sell {
		background: #7f1d1d;
		color: #fecaca;
	}
	.sell-strong {
		color: #f87171 !important;
	}
	.approx {
		display: block;
		color: #64748b;
		font-size: 0.75rem;
		margin-top: 0.15rem;
	}
	.events > li:last-child {
		border-bottom: none;
	}
	.name {
		color: #e2e8f0;
	}
	.pos {
		color: #94a3b8;
	}
	.detail {
		color: #94a3b8;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.detail strong {
		color: #4ade80;
		margin-left: 0.25rem;
	}
</style>

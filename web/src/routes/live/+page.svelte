<script lang="ts">
	import { onDestroy } from 'svelte';
	import { untrack } from 'svelte';

	type Snap = {
		ask_price: number | null;
		ask_size: number | null;
		bid_price: number | null;
		bid_size: number | null;
		spread: number | null;
		last_price: number | null;
		last_size: number | null;
		last_timestamp: string | null;
		quote_timestamp: string | null;
		prev_close: number | null;
		day_change_pct: number | null;
	};

	let enabled = $state<boolean | null>(null);
	let watchInput = $state('NVDA,AAPL,MSFT,TSLA,META,AMZN,GOOGL,SPY');
	let watchlist = $state<string[]>([]);
	let snaps = $state<Record<string, Snap>>({});
	let lastUpdated = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let autoRefresh = $state(true);
	let refreshInterval = $state(5); // seconds
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	async function checkEnabled() {
		try {
			const r = await fetch('/api/live/enabled');
			if (r.ok) enabled = (await r.json()).enabled;
		} catch {
			enabled = false;
		}
	}

	async function fetchSnaps() {
		if (!watchlist.length) return;
		loading = true;
		try {
			const r = await fetch(`/api/live?tickers=${watchlist.join(',')}`);
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			const body = await r.json();
			if (body.error === 'alpaca_not_configured') {
				enabled = false;
				return;
			}
			snaps = body;
			lastUpdated = new Date().toLocaleTimeString();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function applyWatchlist() {
		watchlist = watchInput
			.split(/[,\s]+/)
			.map((t) => t.trim().toUpperCase())
			.filter(Boolean)
			.slice(0, 200);
		if (watchlist.length) fetchSnaps();
	}

	function startPolling() {
		stopPolling();
		if (autoRefresh && watchlist.length && enabled) {
			pollTimer = setInterval(fetchSnaps, Math.max(2, refreshInterval) * 1000);
		}
	}
	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	onDestroy(stopPolling);

	$effect(() => {
		// Track changes to autoRefresh + interval + watchlist length
		autoRefresh;
		refreshInterval;
		watchlist.length;
		enabled;
		untrack(() => startPolling());
	});

	$effect(() => {
		untrack(async () => {
			await checkEnabled();
			applyWatchlist();
		});
	});

	function fmtP(v: number | null | undefined, digits = 2): string {
		return v == null ? '—' : v.toFixed(digits);
	}
	function fmtPct(v: number | null | undefined): string {
		if (v == null) return '—';
		const sign = v >= 0 ? '+' : '';
		return sign + (v * 100).toFixed(2) + '%';
	}
	function fmtTs(ts: string | null | undefined): string {
		if (!ts) return '—';
		try {
			const d = new Date(ts);
			return d.toLocaleTimeString();
		} catch {
			return '—';
		}
	}
</script>

<svelte:head><title>Live quotes · secLab</title></svelte:head>

<h1>Live quotes <span class="panel-sub">— real-time IEX feed via Alpaca</span></h1>
<p class="subtitle">
	Real-time bid / ask / last from the IEX exchange, polled every {refreshInterval}s. Free tier
	shows IEX venue activity only (roughly 2-3% of total US equity volume) — for thinly traded
	small-caps this is often the whole tape; for mega-caps like NVDA you'll see scattered prints.
	Day change % is computed vs previous IEX close.
</p>

{#if error}<div class="error">{error}</div>{/if}

{#if enabled === null}
	<p class="status">Checking provider…</p>
{:else if enabled === false}
	<section class="panel warn">
		<h2>Alpaca not configured</h2>
		<p>
			Add these two lines to your <code>.env</code> file, then restart the API:
		</p>
		<pre>ALPACA_API_KEY_ID=pkxxxxxxxxxxxxxxxxx
ALPACA_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</pre>
		<p>
			Free signup at
			<a href="https://alpaca.markets/" target="_blank" rel="noreferrer">alpaca.markets</a> — email
			only, no credit card. Paper trading account is fine; the data API works independently.
		</p>
	</section>
{:else}
	<section class="panel">
		<div class="controls">
			<label>
				<span>Watchlist</span>
				<input
					type="text"
					bind:value={watchInput}
					placeholder="NVDA,AAPL,MSFT…"
					onblur={applyWatchlist}
					onkeydown={(e) => {
						if (e.key === 'Enter') applyWatchlist();
					}}
				/>
			</label>
			<label class="checkbox">
				<input type="checkbox" bind:checked={autoRefresh} />
				<span>Auto-refresh</span>
			</label>
			<label>
				<span>Every (s)</span>
				<input
					type="number"
					min="2"
					max="60"
					bind:value={refreshInterval}
					style="width:4rem"
				/>
			</label>
			<button onclick={fetchSnaps} disabled={loading || !watchlist.length}>
				{loading ? '…' : 'Refresh now'}
			</button>
			{#if lastUpdated}
				<span class="meta">Last: {lastUpdated}</span>
			{/if}
		</div>
	</section>

	{#if watchlist.length > 0}
		<section class="panel">
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th>Ticker</th>
							<th class="num">Bid</th>
							<th class="num">Ask</th>
							<th class="num">Spread</th>
							<th class="num">Last</th>
							<th class="num">Size</th>
							<th class="num">Day Δ</th>
							<th class="num">Prev close</th>
							<th>Trade time</th>
						</tr>
					</thead>
					<tbody>
						{#each watchlist as t}
							{@const s = snaps[t]}
							<tr>
								<td class="ticker">{t}</td>
								{#if s}
									<td class="num">${fmtP(s.bid_price)}</td>
									<td class="num">${fmtP(s.ask_price)}</td>
									<td class="num muted">{s.spread != null ? '$' + fmtP(s.spread, 3) : '—'}</td>
									<td class="num strong">${fmtP(s.last_price)}</td>
									<td class="num muted">{s.last_size ?? '—'}</td>
									<td
										class="num"
										class:up={(s.day_change_pct ?? 0) >= 0}
										class:down={(s.day_change_pct ?? 0) < 0}
									>
										{fmtPct(s.day_change_pct)}
									</td>
									<td class="num muted">${fmtP(s.prev_close)}</td>
									<td class="muted">{fmtTs(s.last_timestamp)}</td>
								{:else}
									<td class="num muted" colspan="8">loading…</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
{/if}

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 1.75rem;
	}
	h2 {
		margin: 0 0 0.5rem;
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
	.panel {
		background: #0f172a;
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1.25rem;
	}
	.panel.warn {
		border-left: 3px solid #f59e0b;
	}
	.controls {
		display: flex;
		gap: 1rem;
		align-items: flex-end;
		flex-wrap: wrap;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: #94a3b8;
	}
	label.checkbox {
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
	}
	input {
		background: #1e293b;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	input[type='text'] {
		min-width: 320px;
	}
	button {
		background: #2563eb;
		color: white;
		border: none;
		padding: 0.5rem 1rem;
		border-radius: 4px;
		font-size: 0.88rem;
		cursor: pointer;
	}
	button:disabled {
		background: #475569;
		cursor: default;
	}
	.meta {
		color: #cbd5e1;
		font-size: 0.85rem;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 0.5rem 0.75rem;
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
	td.strong {
		font-weight: 600;
	}
	td.muted {
		color: #94a3b8;
	}
	.up {
		color: #4ade80;
	}
	.down {
		color: #f87171;
	}
	pre {
		background: #020617;
		padding: 0.75rem 1rem;
		border-radius: 4px;
		color: #cbd5e1;
		font-size: 0.85rem;
		overflow-x: auto;
	}
	code {
		background: #1e293b;
		padding: 0.1rem 0.35rem;
		border-radius: 3px;
		font-size: 0.85em;
	}
	a {
		color: #60a5fa;
	}
	.error {
		background: #7f1d1d;
		color: #fecaca;
		padding: 0.75rem 1rem;
		border-radius: 4px;
		margin-bottom: 1rem;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
	}
</style>

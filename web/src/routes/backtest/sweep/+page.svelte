<script lang="ts">
	type Combo = {
		preset: string;
		label: string;
		hold_days: number;
		stop_loss_pct: number;
		profit_target_pct: number;
		trailing_stop_pct: number;
		n_trades: number | null;
		win_rate: number | null;
		total_return_pct: number | null;
		cagr: number | null;
		sharpe: number | null;
		sortino: number | null;
		max_drawdown_pct: number | null;
		avg_return_pct: number | null;
		benchmark_return_pct: number | null;
		alpha_vs_benchmark_pct: number | null;
	};

	type PresetSummary = {
		preset: string;
		label: string;
		signals: number;
		combos_count: number;
		error?: string | null;
	};

	type SweepResponse = {
		presets: PresetSummary[];
		range: { start: string; end: string };
		grid_size_per_preset: number;
		total_combos: number;
		combos: Combo[];
		top_by_sharpe: Combo[];
		top_by_alpha: Combo[];
		error?: string;
	};

	type Preset = { key: string; label: string };

	const today = new Date();
	const todayIso = today.toISOString().slice(0, 10);
	const ytdStart = `${today.getFullYear() - 3}-01-01`;

	let presets = $state<Preset[]>([]);
	let selectedPresets = $state<Set<string>>(new Set(['cluster_buys']));
	let start = $state(ytdStart);
	let end = $state(todayIso);
	// Stored as comma-separated strings for easy form editing
	let holdDays = $state('10,20,40');
	let stopLoss = $state('0,5,8,10'); // percent
	let profitTarget = $state('0,15,25'); // percent
	let trailingStop = $state('0');
	let positionMode = $state<'fixed_usd' | 'pct_portfolio'>('fixed_usd');
	let positionSize = $state(10000);
	let positionPctPct = $state(2);
	let benchmark = $state('SPY');
	let slippageBps = $state(10);
	let commissionPerTrade = $state(1);
	let onePositionPerTicker = $state(false);

	function togglePreset(key: string) {
		const next = new Set(selectedPresets);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		selectedPresets = next;
	}

	let data = $state<SweepResponse | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let sortBy = $state<keyof Combo>('sharpe');
	let descending = $state(true);

	async function loadPresets() {
		const r = await fetch('/api/backtest/presets');
		if (r.ok) presets = (await r.json()).presets ?? [];
	}
	$effect(() => {
		loadPresets();
	});

	function toFracList(s: string): string {
		return s
			.split(',')
			.map((x) => x.trim())
			.filter(Boolean)
			.map((x) => String(Number(x) / 100))
			.join(',');
	}

	async function run() {
		if (selectedPresets.size === 0) {
			error = 'Select at least one preset.';
			return;
		}
		loading = true;
		error = null;
		data = null;
		try {
			const params = new URLSearchParams({
				preset: Array.from(selectedPresets).join(','),
				start,
				end,
				hold_days: holdDays,
				stop_loss_pct: toFracList(stopLoss),
				profit_target_pct: toFracList(profitTarget),
				trailing_stop_pct: toFracList(trailingStop),
				position_mode: positionMode,
				position_size: String(positionSize),
				position_pct: String(positionPctPct / 100),
				benchmark,
				slippage_bps: String(slippageBps),
				commission_per_trade: String(commissionPerTrade),
				one_position_per_ticker: String(onePositionPerTicker)
			});
			const r = await fetch(`/api/backtest/sweep?${params}`);
			if (!r.ok) {
				const body = await r.json().catch(() => null);
				throw new Error(body?.detail ?? `${r.status} ${r.statusText}`);
			}
			data = await r.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	const sortedCombos = $derived.by(() => {
		if (!data?.combos) return [] as Combo[];
		const arr = [...data.combos];
		arr.sort((a, b) => {
			const av = (a[sortBy] as number | null) ?? -1e9;
			const bv = (b[sortBy] as number | null) ?? -1e9;
			return descending ? bv - av : av - bv;
		});
		return arr;
	});

	function toggleSort(col: keyof Combo) {
		if (sortBy === col) descending = !descending;
		else {
			sortBy = col;
			descending = true;
		}
	}

	const fmtPct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
	const fmtSignedPct = (v: number | null) =>
		v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
	const fmtNum = (v: number | null, digits = 2) =>
		v == null ? '—' : v.toFixed(digits);

	function sharpeBg(v: number | null): string {
		if (v == null) return 'transparent';
		// Green for high Sharpe, red for negative
		if (v >= 1.5) return 'rgba(34,197,94,0.35)';
		if (v >= 1.0) return 'rgba(34,197,94,0.22)';
		if (v >= 0.5) return 'rgba(74,222,128,0.12)';
		if (v >= 0) return 'rgba(148,163,184,0.08)';
		return 'rgba(239,68,68,0.18)';
	}
	function alphaBg(v: number | null): string {
		if (v == null) return 'transparent';
		if (v >= 0.15) return 'rgba(34,197,94,0.35)';
		if (v >= 0.05) return 'rgba(34,197,94,0.22)';
		if (v >= 0) return 'rgba(74,222,128,0.10)';
		return 'rgba(239,68,68,0.18)';
	}
</script>

<h1>🔎 Parameter sweep</h1>
<p class="subtitle">
	Run a preset across every combo of <code>hold_days × stop_loss × profit_target</code> to find
	the configuration that actually beats SPY. Prices are fetched once and shared across all
	iterations, so a 30-combo sweep usually takes a minute or two.
</p>

<section class="presets-picker">
	<div class="presets-header">
		<span>Presets to sweep ({selectedPresets.size} selected)</span>
		<button class="chip-action" onclick={() => (selectedPresets = new Set(presets.map((p) => p.key)))}>
			all
		</button>
		<button class="chip-action" onclick={() => (selectedPresets = new Set())}>none</button>
	</div>
	<div class="preset-chips">
		{#each presets as p}
			<button
				class="preset-chip"
				class:active={selectedPresets.has(p.key)}
				class:stacked={p.key.startsWith('stacked_')}
				onclick={() => togglePreset(p.key)}
			>
				{p.label}
			</button>
		{/each}
	</div>
</section>

<section class="controls">
	<label>
		<span>Start</span>
		<input type="date" bind:value={start} />
	</label>
	<label>
		<span>End</span>
		<input type="date" bind:value={end} />
	</label>
	<label>
		<span>Hold days (CSV)</span>
		<input type="text" bind:value={holdDays} />
	</label>
	<label>
		<span>Stop-loss % (CSV, 0 = off)</span>
		<input type="text" bind:value={stopLoss} />
	</label>
	<label>
		<span>Profit target % (CSV, 0 = off)</span>
		<input type="text" bind:value={profitTarget} />
	</label>
	<label>
		<span>Trailing stop % (CSV, 0 = off)</span>
		<input type="text" bind:value={trailingStop} />
	</label>
	<label>
		<span>Sizing</span>
		<select bind:value={positionMode}>
			<option value="fixed_usd">Fixed $</option>
			<option value="pct_portfolio">% of portfolio</option>
		</select>
	</label>
	{#if positionMode === 'fixed_usd'}
		<label><span>Position $</span><input type="number" min="100" bind:value={positionSize} /></label>
	{:else}
		<label><span>Position % of equity</span><input type="number" min="0.1" max="100" step="0.5" bind:value={positionPctPct} /></label>
	{/if}
	<label><span>Slippage (bps)</span><input type="number" min="0" max="500" step="1" bind:value={slippageBps} /></label>
	<label><span>Commission $/trade</span><input type="number" min="0" step="0.5" bind:value={commissionPerTrade} /></label>
	<label class="chkbox"><input type="checkbox" bind:checked={onePositionPerTicker} /> <span>1 position / ticker</span></label>
	<label>
		<span>Benchmark</span>
		<input type="text" bind:value={benchmark} size="5" />
	</label>
	<button onclick={run} disabled={loading}>{loading ? 'Running…' : 'Run sweep'}</button>
</section>

<section class="quick-links">
	<a href="/backtest">← back to single backtest</a>
</section>

{#if error}<div class="error">Error: {error}</div>{/if}

{#if loading}
	<div class="status">
		Running sweep — <strong>{holdDays.split(',').length * stopLoss.split(',').length * profitTarget.split(',').length * trailingStop.split(',').length}</strong>
		combos × ~thousands of trades each. Expect 1-5 min.
	</div>
{:else if data && !data.error}
	<section class="meta">
		<span>{data.range.start} → {data.range.end}</span>
		<span>· {data.presets.length} preset{data.presets.length === 1 ? '' : 's'}</span>
		<span>· <strong>{data.total_combos}</strong> combos tested</span>
	</section>
	<section class="preset-summary">
		{#each data.presets as p}
			<div class="preset-stat" class:error={!!p.error}>
				<span class="p-label">{p.label}</span>
				<span class="p-signals">{p.signals?.toLocaleString() ?? '—'} signals</span>
				<span class="p-combos">× {p.combos_count} combos</span>
				{#if p.error}<span class="p-err">{p.error}</span>{/if}
			</div>
		{/each}
	</section>

	<section class="top-grid">
		<section class="panel">
			<h2>🏆 Top 10 by Sharpe</h2>
			<ol class="top-list">
				{#each data.top_by_sharpe as c, i}
					<li>
						<span class="rank">#{i + 1}</span>
						<span class="preset-tag">{c.preset}</span>
						<span class="params">
							hold={c.hold_days} · stop={(c.stop_loss_pct * 100).toFixed(0)}% · tgt={(c.profit_target_pct * 100).toFixed(0)}%
						</span>
						<span class="metric">Sharpe {fmtNum(c.sharpe)}</span>
						<span class="metric alpha">α {fmtSignedPct(c.alpha_vs_benchmark_pct)}</span>
					</li>
				{/each}
			</ol>
		</section>
		<section class="panel">
			<h2>🎯 Top 10 by alpha vs {benchmark}</h2>
			<ol class="top-list">
				{#each data.top_by_alpha as c, i}
					<li>
						<span class="rank">#{i + 1}</span>
						<span class="preset-tag">{c.preset}</span>
						<span class="params">
							hold={c.hold_days} · stop={(c.stop_loss_pct * 100).toFixed(0)}% · tgt={(c.profit_target_pct * 100).toFixed(0)}%
						</span>
						<span class="metric alpha">α {fmtSignedPct(c.alpha_vs_benchmark_pct)}</span>
						<span class="metric">Sharpe {fmtNum(c.sharpe)}</span>
					</li>
				{/each}
			</ol>
		</section>
	</section>

	<section class="panel">
		<h2>All combos ({sortedCombos.length})</h2>
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th class="sortable" onclick={() => toggleSort('preset')}>Preset {sortBy === 'preset' ? (descending ? '↓' : '↑') : ''}</th>
						<th class="sortable" onclick={() => toggleSort('hold_days')}>Hold {sortBy === 'hold_days' ? (descending ? '↓' : '↑') : ''}</th>
						<th class="sortable" onclick={() => toggleSort('stop_loss_pct')}>Stop {sortBy === 'stop_loss_pct' ? (descending ? '↓' : '↑') : ''}</th>
						<th class="sortable" onclick={() => toggleSort('profit_target_pct')}>Target {sortBy === 'profit_target_pct' ? (descending ? '↓' : '↑') : ''}</th>
						<th class="sortable num" onclick={() => toggleSort('n_trades')}>Trades</th>
						<th class="sortable num" onclick={() => toggleSort('win_rate')}>Win</th>
						<th class="sortable num" onclick={() => toggleSort('total_return_pct')}>Total</th>
						<th class="sortable num" onclick={() => toggleSort('cagr')}>CAGR</th>
						<th class="sortable num" onclick={() => toggleSort('sharpe')}>Sharpe {sortBy === 'sharpe' ? (descending ? '↓' : '↑') : ''}</th>
						<th class="sortable num" onclick={() => toggleSort('max_drawdown_pct')}>MaxDD</th>
						<th class="sortable num" onclick={() => toggleSort('alpha_vs_benchmark_pct')}>α {sortBy === 'alpha_vs_benchmark_pct' ? (descending ? '↓' : '↑') : ''}</th>
					</tr>
				</thead>
				<tbody>
					{#each sortedCombos as c}
						<tr>
							<td class="preset-cell" title={c.label}>{c.preset}</td>
							<td class="num">{c.hold_days}</td>
							<td class="num">{(c.stop_loss_pct * 100).toFixed(0)}%</td>
							<td class="num">{(c.profit_target_pct * 100).toFixed(0)}%</td>
							<td class="num">{c.n_trades?.toLocaleString() ?? '—'}</td>
							<td class="num">{fmtPct(c.win_rate)}</td>
							<td class="num">{fmtPct(c.total_return_pct)}</td>
							<td class="num">{fmtPct(c.cagr)}</td>
							<td class="num sharpe-cell" style="background: {sharpeBg(c.sharpe)}">{fmtNum(c.sharpe)}</td>
							<td class="num">{fmtPct(c.max_drawdown_pct)}</td>
							<td class="num alpha-cell" style="background: {alphaBg(c.alpha_vs_benchmark_pct)}">{fmtSignedPct(c.alpha_vs_benchmark_pct)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{:else if data?.error}
	<div class="warning">{data.error}</div>
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
	.subtitle code {
		background: #0f172a;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
	}
	.controls {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0.75rem;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
		margin: 1.5rem 0 0.75rem;
	}
	.controls label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.75rem;
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
		padding: 0.55rem 1rem;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.9rem;
		align-self: end;
	}
	.controls button:disabled {
		background: #475569;
	}
	.controls label.chkbox { flex-direction: row; align-items: center; gap: 0.4rem; }
	.controls label.chkbox input { width: auto; }
	.controls select {
		background: #0f172a;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.4rem 0.6rem;
		border-radius: 4px;
		font-size: 0.9rem;
	}
	.presets-picker {
		background: #1e293b;
		padding: 0.75rem 1rem;
		border-radius: 8px;
		margin: 1.5rem 0 0.75rem;
	}
	.presets-header {
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
	.preset-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.preset-chip {
		background: #0f172a;
		border: 1px solid #334155;
		color: #94a3b8;
		padding: 0.35rem 0.7rem;
		border-radius: 999px;
		font-size: 0.8rem;
		cursor: pointer;
	}
	.preset-chip:hover {
		border-color: #60a5fa;
	}
	.preset-chip.active {
		background: #1e3a8a;
		color: #e0f2fe;
		border-color: #60a5fa;
	}
	.preset-chip.stacked {
		border-style: dashed;
	}
	.preset-chip.stacked.active {
		background: #14532d;
		border-color: #4ade80;
	}
	.preset-summary {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.preset-stat {
		background: #1e293b;
		padding: 0.5rem 0.75rem;
		border-radius: 6px;
		font-size: 0.8rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.preset-stat.error {
		background: #7f1d1d40;
	}
	.p-label {
		color: #e2e8f0;
	}
	.p-signals,
	.p-combos {
		color: #94a3b8;
		font-variant-numeric: tabular-nums;
	}
	.p-err {
		color: #fecaca;
	}
	.preset-tag {
		background: #0f172a;
		border: 1px solid #334155;
		color: #94a3b8;
		padding: 0.1rem 0.4rem;
		border-radius: 3px;
		font-size: 0.7rem;
		font-variant-numeric: tabular-nums;
	}
	.preset-cell {
		color: #94a3b8;
		font-size: 0.75rem;
	}
	.quick-links {
		margin-bottom: 1rem;
		font-size: 0.85rem;
	}
	.quick-links a {
		color: #60a5fa;
		text-decoration: none;
	}
	.meta {
		display: flex;
		gap: 0.5rem;
		color: #94a3b8;
		font-size: 0.9rem;
		margin-bottom: 1rem;
		flex-wrap: wrap;
	}
	.top-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	@media (max-width: 900px) {
		.top-grid {
			grid-template-columns: 1fr;
		}
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
	.top-list {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.top-list li {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid #334155;
		font-size: 0.85rem;
		align-items: baseline;
	}
	.top-list li:last-child {
		border-bottom: none;
	}
	.rank {
		color: #94a3b8;
		font-weight: 600;
		min-width: 2.5rem;
	}
	.params {
		color: #e2e8f0;
		font-variant-numeric: tabular-nums;
		flex: 1;
		min-width: 220px;
	}
	.metric {
		color: #cbd5e1;
		font-variant-numeric: tabular-nums;
		font-size: 0.8rem;
	}
	.metric.alpha {
		color: #4ade80;
	}
	.table-wrap {
		overflow: auto;
		max-height: 620px;
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
	th.sortable {
		cursor: pointer;
		user-select: none;
	}
	th.sortable:hover {
		color: #e2e8f0;
	}
	.sharpe-cell,
	.alpha-cell {
		font-weight: 600;
	}
	.status {
		color: #94a3b8;
		font-style: italic;
		padding: 1rem;
		background: #1e293b;
		border-radius: 8px;
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
</style>

<script lang="ts">
	import { goto } from '$app/navigation';

	type Props = {
		autofocus?: boolean;
		placeholder?: string;
		hrefBase?: string; // e.g. /fundamentals — selected ticker appended
		onSelect?: (ticker: string, name: string) => void;
	};

	let {
		autofocus = false,
		placeholder = 'Search ticker or company…',
		hrefBase = '/fundamentals',
		onSelect
	}: Props = $props();

	type Result = { ticker: string; cik: number; name: string };

	let q = $state('');
	let results = $state<Result[]>([]);
	let open = $state(false);
	let highlighted = $state(0);
	let inputEl: HTMLInputElement | undefined = $state();

	let debounceTimer: ReturnType<typeof setTimeout> | undefined;

	function queryApi(v: string) {
		clearTimeout(debounceTimer);
		if (!v.trim()) {
			results = [];
			open = false;
			return;
		}
		debounceTimer = setTimeout(async () => {
			try {
				const r = await fetch(
					`/api/tickers/search?q=${encodeURIComponent(v)}&limit=12`
				);
				if (!r.ok) return;
				const j = await r.json();
				results = j.results ?? [];
				highlighted = 0;
				open = results.length > 0;
			} catch {
				results = [];
			}
		}, 120);
	}

	$effect(() => {
		queryApi(q);
	});

	function pick(r: Result) {
		q = r.ticker;
		open = false;
		if (onSelect) onSelect(r.ticker, r.name);
		else goto(`${hrefBase}/${encodeURIComponent(r.ticker)}`);
	}

	function onKey(e: KeyboardEvent) {
		if (!open || !results.length) {
			if (e.key === 'Enter' && q.trim()) {
				goto(`${hrefBase}/${encodeURIComponent(q.trim().toUpperCase())}`);
			}
			return;
		}
		if (e.key === 'ArrowDown') {
			highlighted = (highlighted + 1) % results.length;
			e.preventDefault();
		} else if (e.key === 'ArrowUp') {
			highlighted = (highlighted - 1 + results.length) % results.length;
			e.preventDefault();
		} else if (e.key === 'Enter') {
			pick(results[highlighted]);
			e.preventDefault();
		} else if (e.key === 'Escape') {
			open = false;
		}
	}

	$effect(() => {
		if (autofocus && inputEl) inputEl.focus();
	});
</script>

<div class="wrap">
	<input
		bind:this={inputEl}
		type="text"
		bind:value={q}
		onkeydown={onKey}
		onfocus={() => q && results.length && (open = true)}
		onblur={() => setTimeout(() => (open = false), 120)}
		{placeholder}
		autocomplete="off"
		spellcheck="false"
	/>
	{#if open && results.length}
		<ul class="dropdown" role="listbox">
			{#each results as r, i}
				<li
					class:hl={i === highlighted}
					role="option"
					aria-selected={i === highlighted}
					onmousedown={(e) => {
						e.preventDefault();
						pick(r);
					}}
					onmouseenter={() => (highlighted = i)}
				>
					<span class="tic">{r.ticker}</span>
					<span class="nm">{r.name}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.wrap {
		position: relative;
		flex: 1;
	}
	input {
		width: 100%;
		background: #1e293b;
		border: 1px solid #334155;
		color: #e2e8f0;
		padding: 0.6rem 0.75rem;
		border-radius: 4px;
		font-size: 0.95rem;
		box-sizing: border-box;
	}
	input:focus {
		outline: none;
		border-color: #60a5fa;
	}
	.dropdown {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		background: #0f172a;
		border: 1px solid #334155;
		border-radius: 4px;
		max-height: 340px;
		overflow-y: auto;
		margin: 0;
		padding: 0.25rem 0;
		list-style: none;
		z-index: 50;
		box-shadow: 0 12px 24px rgba(0, 0, 0, 0.4);
	}
	.dropdown li {
		display: grid;
		grid-template-columns: 5.5rem 1fr;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		cursor: pointer;
		font-size: 0.9rem;
	}
	.dropdown li.hl {
		background: #1e3a8a;
	}
	.tic {
		color: #60a5fa;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.nm {
		color: #cbd5e1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>

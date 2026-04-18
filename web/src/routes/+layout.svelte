<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/state';
	import { browser } from '$app/environment';

	let { children } = $props();

	// Grouped nav for the sidebar. Group labels are small caps above each list.
	const navGroups: { heading: string; items: { href: string; label: string }[] }[] = [
		{
			heading: 'Home',
			items: [{ href: '/', label: 'Home' }]
		},
		{
			heading: 'Ownership signals',
			items: [
				{ href: '/insider-buying', label: 'Insider Buying' },
				{ href: '/activism', label: 'Activism (13D)' },
				{ href: '/schedule-13g', label: 'Passive 5%+ (13G)' },
				{ href: '/form-3', label: 'New Insiders (Form 3)' },
				{ href: '/superinvestors', label: 'Superinvestors (13F)' }
			]
		},
		{
			heading: 'Event signals',
			items: [
				{ href: '/events', label: 'Events (8-K)' },
				{ href: '/late-filings', label: 'Late Filings' },
				{ href: '/proxy-contests', label: 'Proxy Contests' },
				{ href: '/secondaries', label: 'Secondaries' },
				{ href: '/tender-offers', label: 'Tender Offers' },
				{ href: '/delistings', label: 'Delistings' },
				{ href: '/form-d', label: 'Private Placements (D)' },
				{ href: '/foreign-issuers', label: 'Foreign Issuers' },
				{ href: '/ipos', label: 'IPOs + Lockups' },
				{ href: '/mergers', label: 'M&A (proxies)' }
			]
		},
		{
			heading: 'Flow',
			items: [
				{ href: '/dark-pool', label: 'Off-exchange (ATS + non-ATS)' },
				{ href: '/smart-vs-dumb', label: 'Smart vs dumb money' },
				{ href: '/short-sale', label: 'Short-sale daily' },
				{ href: '/short-pressure', label: 'Short pressure (FTD / SHO / SI)' },
				{ href: '/live', label: 'Live quotes (IEX)' }
			]
		},
		{
			heading: 'Research',
			items: [
				{ href: '/fundamentals/AAPL', label: 'Fundamentals' },
				{ href: '/screener', label: 'Screener' }
			]
		},
		{
			heading: 'Backtesting',
			items: [
				{ href: '/backtest', label: 'Backtest' },
				{ href: '/backtest/sweep', label: 'Param Sweep' },
				{ href: '/backtest/walkforward', label: 'Walk-Forward' },
				{ href: '/paper', label: 'Paper / Forward test' }
			]
		},
		{
			heading: 'Research',
			items: [
				{ href: '/research/halflife', label: 'Half-life compare' }
			]
		}
	];

	// Persist collapsed state across page loads via localStorage.
	let collapsed = $state(false);
	if (browser) {
		const saved = localStorage.getItem('seclab:sidebar');
		if (saved === 'collapsed') collapsed = true;
	}
	function toggle() {
		collapsed = !collapsed;
		if (browser) localStorage.setItem('seclab:sidebar', collapsed ? 'collapsed' : 'open');
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>secLab</title>
</svelte:head>

<div class="app" class:collapsed>
	<aside class="sidebar">
		<div class="brand-row">
			{#if !collapsed}
				<a class="brand" href="/">secLab</a>
			{/if}
			<button class="toggle" onclick={toggle} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand' : 'Collapse'}>
				{collapsed ? '›' : '‹'}
			</button>
		</div>

		<nav>
			{#each navGroups as g}
				{#if !collapsed}
					<div class="group-heading">{g.heading}</div>
				{/if}
				<ul>
					{#each g.items as item}
						<li>
							<a
								href={item.href}
								class:active={page.url.pathname === item.href ||
									(item.href !== '/' && page.url.pathname.startsWith(item.href))}
								title={collapsed ? item.label : ''}
							>
								{#if collapsed}
									<span class="icon">{item.label.slice(0, 2).toUpperCase()}</span>
								{:else}
									{item.label}
								{/if}
							</a>
						</li>
					{/each}
				</ul>
			{/each}
		</nav>
	</aside>

	<main>
		{@render children()}
	</main>
</div>

<style>
	:global(body) {
		margin: 0;
		font-family:
			system-ui,
			-apple-system,
			'Segoe UI',
			sans-serif;
		background: #0f172a;
		color: #e2e8f0;
	}
	.app {
		display: grid;
		grid-template-columns: 240px 1fr;
		min-height: 100vh;
		transition: grid-template-columns 180ms ease;
	}
	.app.collapsed {
		grid-template-columns: 56px 1fr;
	}
	.sidebar {
		background: #020617;
		border-right: 1px solid #1e293b;
		padding: 0.75rem 0;
		position: sticky;
		top: 0;
		height: 100vh;
		overflow-y: auto;
	}
	.brand-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.25rem 0.75rem 1rem;
		border-bottom: 1px solid #1e293b;
		margin-bottom: 0.5rem;
	}
	.brand {
		color: #60a5fa;
		text-decoration: none;
		font-weight: 700;
		font-size: 1.2rem;
	}
	.toggle {
		background: transparent;
		border: 1px solid #334155;
		color: #94a3b8;
		width: 28px;
		height: 28px;
		border-radius: 4px;
		cursor: pointer;
		font-size: 1rem;
		line-height: 1;
	}
	.toggle:hover {
		color: #e2e8f0;
		border-color: #60a5fa;
	}
	nav ul {
		list-style: none;
		margin: 0 0 0.5rem;
		padding: 0;
	}
	.group-heading {
		color: #64748b;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		padding: 0.75rem 0.75rem 0.35rem;
	}
	nav a {
		display: block;
		color: #94a3b8;
		text-decoration: none;
		font-size: 0.9rem;
		padding: 0.4rem 0.75rem;
		border-left: 2px solid transparent;
	}
	nav a:hover {
		color: #e2e8f0;
		background: #0f172a;
	}
	nav a.active {
		color: #60a5fa;
		border-left-color: #60a5fa;
		background: #0f172a;
	}
	.app.collapsed nav a {
		display: flex;
		justify-content: center;
		padding: 0.5rem 0.25rem;
	}
	.icon {
		display: inline-block;
		width: 28px;
		text-align: center;
		color: #94a3b8;
		font-weight: 600;
		font-size: 0.7rem;
		letter-spacing: 0.04em;
	}
	nav a.active .icon {
		color: #60a5fa;
	}
	main {
		padding: 2rem;
		max-width: 1400px;
		width: 100%;
		box-sizing: border-box;
	}
</style>

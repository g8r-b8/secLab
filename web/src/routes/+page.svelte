<script lang="ts">
	import TickerSearch from '$lib/TickerSearch.svelte';

	const cards = [
		{
			href: '/insider-buying',
			title: 'Insider Buying',
			desc: 'SEC Form 4 open-market purchases + Form 144 planned sells, per-ticker event study.'
		},
		{
			href: '/activism',
			title: 'Activism (13D)',
			desc: 'Schedule 13D filings — 5%+ stakes with intent to influence. Academic alpha signal.'
		},
		{
			href: '/events',
			title: 'Events (8-K)',
			desc: 'Material event filings tagged by SEC item code — restatements, exec changes, M&A, buybacks.'
		},
		{
			href: '/late-filings',
			title: 'Late Filings',
			desc: 'NT 10-K / NT 10-Q — companies that missed their filing deadline. Clean bear signal.'
		},
		{
			href: '/proxy-contests',
			title: 'Proxy Contests',
			desc: 'Activist DFAN14A / DEFC14A filings — who\'s nominating a dissident slate against whom.'
		},
		{
			href: '/secondaries',
			title: 'Secondaries',
			desc: 'S-3 / 424B dilution events. Clear bear signal on announcement — repeat offenders especially.'
		},
		{
			href: '/tender-offers',
			title: 'Tender Offers',
			desc: 'SC TO-T / 14D9 — M&A targets and buybacks. Binary events with pre-announced prices.'
		},
		{
			href: '/schedule-13g',
			title: 'Passive 5%+ (13G)',
			desc: 'Schedule 13G — passive institutional 5%+ stake crossings. Volume signal for quiet accumulation.'
		},
		{
			href: '/form-3',
			title: 'New Insiders (Form 3)',
			desc: 'Form 3 — initial statement of beneficial ownership. New director/officer hires, IPO insiders.'
		},
		{
			href: '/form-d',
			title: 'Private Placements',
			desc: 'Form D — Reg D exempt offerings. Microcap fundraising pulse.'
		},
		{
			href: '/delistings',
			title: 'Delistings',
			desc: 'Form 15 / Form 25 — companies leaving public markets. Rare but unambiguous.'
		},
		{
			href: '/foreign-issuers',
			title: 'Foreign Issuers',
			desc: '20-F annual + 6-K current reports. Opens ADR universe (BABA, ASML, TSM, etc).'
		},
		{
			href: '/superinvestors',
			title: 'Superinvestors (13F)',
			desc: 'Quarterly holdings + deltas for Buffett, Burry, Ackman, Tepper, Druckenmiller, Icahn…'
		},
		{
			href: '/screener',
			title: 'Screener',
			desc: 'Cross-sectional ratios across every US-listed ticker. Filter by value, quality, growth.'
		},
		{
			href: '/fundamentals/AAPL',
			title: 'Fundamentals',
			desc: 'XBRL financial statements + key ratios from SEC 10-K filings. Compustat-lite.'
		},
		{
			href: '/backtest',
			title: 'Backtest',
			desc: 'Plug any SEC signal into a daily-bar engine. Equity curve vs SPY, Sharpe, max DD.'
		}
	];
</script>

<h1>secLab</h1>
<p class="subtitle">SEC filing signals.</p>

<div class="ticker-form">
	<TickerSearch autofocus placeholder="Search any US-listed ticker or company name…" />
</div>

<div class="grid">
	{#each cards as c}
		<a href={c.href} class="card">
			<h2>{c.title}</h2>
			<p>{c.desc}</p>
		</a>
	{/each}
</div>

<style>
	h1 {
		margin: 0 0 0.25rem;
		font-size: 2rem;
	}
	.subtitle {
		color: #94a3b8;
		margin-top: 0;
	}
	.ticker-form {
		display: flex;
		max-width: 520px;
		margin: 1.5rem 0;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 1rem;
		margin-top: 2rem;
	}
	.card {
		display: block;
		padding: 1.25rem;
		background: #1e293b;
		border: 1px solid #334155;
		border-radius: 8px;
		text-decoration: none;
		color: inherit;
		transition: border-color 120ms;
	}
	.card:hover {
		border-color: #60a5fa;
	}
	.card h2 {
		margin: 0 0 0.5rem;
		font-size: 1.1rem;
		color: #e2e8f0;
	}
	.card p {
		margin: 0;
		color: #94a3b8;
		font-size: 0.9rem;
	}
</style>

<script lang="ts">
	import FilingsFeedPage from '$lib/FilingsFeedPage.svelte';
</script>

<FilingsFeedPage
	title="🪦 Delistings (Form 15 + Form 25)"
	subtitle="Form 15 — certification of termination of registration (the company goes dark). Form 25 — notice of removal from listing / registration under Section 12(b). Rare but unambiguous signal: the company is leaving the public market."
	endpoint="/api/signals/delistings"
	defaultStartDays={60}
	formLabels={{
		'15-12B': 'Termination of registration (Sec 12(b))',
		'15-12G': 'Termination of registration (Sec 12(g))',
		'15-15D': 'Suspension of reporting obligations',
		'15-12B/A': 'Amendment',
		'15-12G/A': 'Amendment',
		25: 'Removal from listing',
		'25/A': 'Amendment',
		'25-NSE': 'Removal from listing (NSE)',
		'25-NSE/A': 'Amendment'
	}}
/>

<script lang="ts">
	import FilingsFeedPage from '$lib/FilingsFeedPage.svelte';
</script>

<FilingsFeedPage
	title="Foreign Issuers (20-F / 6-K)"
	subtitle="Non-US companies listed as ADRs file 20-F (annual report — like 10-K) and 6-K (current reports — like 8-K). Covers names like BABA, ASML, TSM, SHOP, and thousands of others that our Form 4 / 10-K pipelines miss entirely. 6-K volume is high (~8k/month) so keep the date window narrow."
	endpoint="/api/signals/foreign-issuers"
	defaultStartDays={7}
	formLabels={{
		'20-F': 'Annual report (foreign)',
		'20-F/A': 'Annual report amendment',
		'6-K': 'Current report',
		'6-K/A': 'Current report amendment',
		'NT 20-F': 'Late 20-F notification'
	}}
/>

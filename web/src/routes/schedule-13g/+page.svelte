<script lang="ts">
	import FilingsFeedPage from '$lib/FilingsFeedPage.svelte';
</script>

<FilingsFeedPage
	title="📊 Passive Ownership (SC 13G)"
	subtitle="Schedule 13G filings — someone crossed 5% ownership but without intent to influence management. Index funds, pension funds, passive institutional accumulators. Weaker signal than 13D but a volume indicator for who's quietly loading up."
	endpoint="/api/signals/13g"
	defaultStartDays={30}
	formLabels={{ 'SCHEDULE 13G': 'Passive 5%+ filing', 'SCHEDULE 13G/A': 'Amendment' }}
/>

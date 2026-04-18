<script lang="ts">
	import FilingsFeedPage from '$lib/FilingsFeedPage.svelte';
</script>

<FilingsFeedPage
	title="💰 Private Placements (Form D)"
	subtitle="Form D — notice of exempt offering (Reg D private placement). Companies use these to raise capital from accredited investors without full SEC registration. Microcap fundraising pulse; volume shifts often precede public-market dilution or operating milestones."
	endpoint="/api/signals/form-d"
	defaultStartDays={7}
	formLabels={{ D: 'Private placement notice', 'D/A': 'Amendment' }}
/>

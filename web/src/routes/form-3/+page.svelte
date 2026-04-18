<script lang="ts">
	import FilingsFeedPage from '$lib/FilingsFeedPage.svelte';
</script>

<FilingsFeedPage
	title="🆕 New Insiders (Form 3)"
	subtitle="Form 3 is filed when someone becomes an insider — new director, new officer, or newly-crossed-5% shareholder. Often ties to IPOs, executive appointments, and post-hire share grants. Interesting primarily as a watchlist: subsequent Form 4 buys from a new insider are especially meaningful."
	endpoint="/api/signals/form3"
	defaultStartDays={14}
	formLabels={{ '3': 'Initial beneficial ownership', '3/A': 'Amendment' }}
/>

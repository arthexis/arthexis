# Public charger and connector performance notes

## Dashboard view
- The dashboard view fetches each charger's latest transaction individually when it isn't already cached, leading to an N+1 query pattern across the visible chargers list. Collecting the latest transaction IDs with a subquery or prefetch would trim redundant queries during dashboard refreshes and partial table renders.
- Per-charger energy stats (`total_kw` and `total_kw_for_range`) are computed inside the dashboard loop. Batch annotations for the current day and lifetime totals (or caching a daily summary) would avoid repeating aggregations for every connector on each request.

## Public charger page and connector overview
- `_connector_overview` walks every sibling connector and calls the OCPP store plus `_charger_state` for each. Prefetching sibling connectors before rendering the page (or caching the overview payload) would cut repeated ORM hits for chargers with many connectors.
- The landing page translations are recomputed on every request by iterating through all configured languages and overriding translations. Wrapping the translation catalog in an in-memory cache (for example via `functools.lru_cache`) would eliminate per-request translation churn for the public landing page payload.

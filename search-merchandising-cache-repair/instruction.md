# Search merchandising cache repair

We have a production issue in the search merchandising refresh job. The job is supposed to refresh cached result rows for a search query without trampling rows that another team owns. Recent refreshes have made growth experiment updates look correct on the surface, but a few cached pages keep losing editorial slots, carrying stale product ids, or rewriting rows that should have been sent to review.

The worker reads and writes `/app/catalog.json`. The file is a single JSON document with these top-level keys:

- `generated_at`: ISO timestamp used for experiment selection and refreshed cache rows.
- `default_weights`: numeric weights for `relevance`, `ctr`, `margin`, and `freshness`.
- `experiments`: list of policies with `name`, `query_id`, `owner`, `active_from`, `active_until`, and `weights`.
- `products`: list of product records. Each product has `product_id`, `title`, `category`, `brand`, `in_stock`, `base_score`, `ctr`, `margin`, `recency_days`, and `catalog_version`.
- `searches`: list of search definitions with `query_id`, `search_text`, `category`, `page_size`, `blocked_ids`, `demoted_ids`, and `brand_boosts`.
- `search_results`: cached result records keyed by query id.
- `result_conflicts`: conflict records keyed by query id.

A cached result record has `query_id`, `status`, `attempts`, `generated_at`, `experiment_name`, `owner`, `product_ids`, `scores`, `applied_weights`, `manual_slots`, `rows`, and optional `failure_reason`, `review_required`, `conflict`, `frozen`, and `history`. The retry limit is three attempts.

`manual_slots` is keyed by one-based slot number as a string. A slot value is an object with `product_id` and `owner`. Only editorial manual slots are honored. Editorial manual rows are slot-specific protected merchandising state: keep a valid editorial manual row in its current page position, and send slot moves or other-owner manual hints through the same review path rather than silently applying them.

`rows` is keyed by product id. Each row has `product_id`, `owner`, `score`, `position`, `generated_at`, `experiment_name`, `write_count`, `manual`, and `catalog_version`. Owners can be `editorial`, `growth`, or `system`.

When a protected row blocks a refresh, store the conflict on the cached result as `conflict` and also under `result_conflicts[query_id]`. A protected-cache conflict record has `query_id`, `reason`, `active_owner`, `protected_product_ids`, and `generated_at`. `reason` is `protected_cache_conflict`. Conflict product ids should reflect the final requested page order. Stale rows outside that page are cleanup work, not blockers.

Expected behavior:

- Frozen cached results are not changed.
- Active experiments are scoped to the query and active when `generated_at` is within `[active_from, active_until)`. If multiple experiments match, use the newest `active_from`; otherwise use the default policy with owner `search` and name `default`.
- Eligible products match the search category, are in stock, and are not blocked.
- Score eligible products from weighted `base_score`, `ctr`, `margin`, and freshness. Freshness is `1 - min(max(recency_days, 0), 30) / 30`. Apply brand boosts and demotions after the weighted score. A demotion subtracts `0.35`.
- Sort by score descending, then `product_id` ascending.
- Preserve valid editorial manual slots before filling the rest of the page from scored products. Drop manual slots for blocked, unavailable, wrong-category, duplicate, or out-of-page products.
- A refresh owned by one team must not overwrite a cached row owned by another team. A valid editorial manual row in its protected manual slot is the exception; keep it in place.
- When a protected owner conflict blocks a refresh, keep the existing rows unchanged, set `failure_reason` to `protected_cache_conflict`, set `review_required` to true, and write the conflict record described above. If `attempts` is below the retry limit, increment it once and leave the result in `retrying`. If `attempts` is already at the limit, mark the result `failed` and do not increment it past the limit.
- Partial refreshes should reuse fresh rows for the active owner, refresh stale rows for the active owner, and remove rows for products no longer requested. Product revision is part of cache freshness, so refreshed non-manual rows should carry the current `catalog_version`.
- Successful refreshes clear stale failure and conflict fields.
- `product_ids` should match the final page order, with stale ids and duplicates removed.
- Existing valid cache rows keep their `write_count`. Rows that are actually refreshed increment their own `write_count`.
- The worker should return exactly the document that it persisted.

Keep the fix inside `/app`.

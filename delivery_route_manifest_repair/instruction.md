A delivery company runs a repair worker after late package scans arrive from driver handheld devices. The worker rebuilds route manifest rows for one driver route, but the current implementation rewrites terminal jobs, duplicates audit rows, accepts partial source data, and overwrites stops already protected by another active repair job.

Run the worker with:

```bash
python /app/manifest_worker.py
```

The worker must read `/app/route_state.json`, process jobs in place, write the same JSON document back to `/app/route_state.json`, and return the persisted document from `run()`.

Top-level keys:

- `stops`: object keyed by stop id. Each stop has `route_id`, `driver_id`, `address`, `packages`, and `scan_version`.
- `manifests`: object keyed by stop id. Each manifest row has `stop_id`, `route_id`, `driver_id`, `package_ids`, `owner_job_id`, `source_version`, and `status`.
- `audit_log`: list of rows with `job_id`, `stop_id`, `event`, and `source_version`.
- `jobs`: list of repair jobs.

Each job has `id`, `route_id`, `driver_id`, `expected_stop_ids`, `status`, `attempts`, and `max_attempts`. Jobs may also have `last_error`, `retry_reason`, `touched_stop_ids`, `summary`, and `metadata`.

Valid statuses are `pending`, `retrying`, `completed`, `failed`, and `cancelled`. `completed`, `failed`, and `cancelled` are terminal and must not be rewritten.

Process only `pending` and `retrying` jobs.

Normalize `expected_stop_ids` by removing duplicate ids while keeping first-seen order.

Before writing any manifest row for a job, preflight every normalized stop id:

- the stop must exist
- the stop must match the job `route_id`
- the stop must match the job `driver_id`
- an existing manifest row owned by another non-terminal job is protected and must not be overwritten

If a stop is missing or mismatched, retry when another attempt is available and fail when attempts are exhausted. Use retry reason `source_unavailable`. Do not write partial manifest rows or audit rows for source-data failures.

If a protected manifest row is found, leave that row and audit log unchanged for this job. Use retry reason `manifest_conflict` and retry or fail based on attempts.

For each valid stop, reuse a same-owner manifest row when `source_version` still matches the stop `scan_version`. Refresh stale same-owner rows. A row owned by a terminal job may be reclaimed. `package_ids` must be deduplicated while keeping order.

When a job completes, set `touched_stop_ids` to the normalized ids and `summary` to `stop_count` and `package_count`. Clear stale `last_error` and `retry_reason`, but keep unrelated `metadata`.

Audit rows are idempotent. There must be at most one `manifested` row for the same `job_id`, `stop_id`, and `source_version`. Repeated runs must be stable.

Keep the fix inside `/app`. Do not modify tests or reward files.

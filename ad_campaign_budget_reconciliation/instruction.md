An ad platform runs a repair worker after delayed click-spend events arrive from exchange partners. The worker rebuilds campaign budget ledger rows for a reporting window, but the current implementation double-counts duplicate events, rewrites manual holds, accepts out-of-window events, and overwrites ledger rows owned by another active reconciliation job.

Run the worker with:

```bash
python /app/budget_worker.py
```

The worker must read `/app/ad_state.json`, process jobs in place, write the same JSON document back to `/app/ad_state.json`, and return the persisted document from `run()`.

Top-level keys:

- `campaigns`: object keyed by campaign id. Each campaign has `account_id`, `daily_budget_cents`, and `status`.
- `spend_events`: object keyed by event id. Each event has `campaign_id`, `account_id`, `window`, `amount_cents`, and `version`.
- `budget_ledger`: object keyed by event id. Each row has `event_id`, `campaign_id`, `account_id`, `window`, `amount_cents`, `owner_job_id`, `source_version`, and `kind`.
- `manual_holds`: object keyed by hold id. These entries are outside the repair and must be preserved exactly.
- `jobs`: list of reconciliation jobs.

Each job has `id`, `campaign_id`, `account_id`, `window`, `expected_event_ids`, `status`, `attempts`, and `max_attempts`. Jobs may also have `last_error`, `retry_reason`, `processed_event_ids`, `summary`, and `metadata`.

Valid statuses are `pending`, `retrying`, `completed`, `failed`, and `cancelled`. `completed`, `failed`, and `cancelled` are terminal and must not be rewritten.

Process only `pending` and `retrying` jobs.

Normalize `expected_event_ids` by removing duplicates while keeping first-seen order.

Before writing ledger rows, preflight the normalized event ids:

- the campaign must exist, match the job `account_id`, and have status `active`
- every event must exist
- every event must match the job `campaign_id`, `account_id`, and `window`
- a ledger row owned by another non-terminal job is protected and must not be overwritten

If a campaign or event is unavailable or mismatched, retry when another attempt is available and fail when attempts are exhausted. Use retry reason `source_unavailable`. Do not write partial ledger rows for source-data failures.

If a protected ledger row is found, leave `budget_ledger` and `manual_holds` unchanged for this job. Use retry reason `ledger_conflict` and retry or fail based on attempts.

For valid events, reuse a same-owner ledger row when `source_version` still matches the event `version`. Refresh stale same-owner rows. A row owned by a terminal job may be reclaimed. Ledger row `kind` must be `exchange_spend`.

When a job completes, set `processed_event_ids` to the normalized ids and set `summary` with `event_count`, `spend_cents`, and `remaining_budget_cents`. Remaining budget is the campaign daily budget minus spend from the normalized events only. Clear stale `last_error` and `retry_reason`, but keep unrelated `metadata`.

Manual holds must never be removed or rewritten. Repeated runs must not double-count spend or change completed jobs.

Keep the fix inside `/app`. Do not modify tests or reward files.

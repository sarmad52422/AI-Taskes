A marketplace payout service keeps a persisted funds-lock document in `/app/payout_locks.json`. The nightly worker at `/app/payout_worker.py` is supposed to reserve shelf funds for vendor payouts before packing starts, but a recent change made it rewrite terminal payouts, duplicate locks, leave stale retry metadata behind, and overwrite locks created by other systems.

Run the worker with:

```bash
python /app/payout_worker.py
```

The worker must load `/app/payout_locks.json`, process eligible payouts, save the same JSON file, and return the persisted document from `run()`.

The JSON document has these top-level keys:

- `balances`: object keyed by vendor id. Each vendor has `available_cents` as an integer.
- `payouts`: list of payout objects.
- `locks`: list of lock rows.
- `stats`: object with counters such as `locks_created`, `locks_refreshed`, `conflicts`, and `failures`.

Each payout has:

- `id`: stable payout id.
- `status`: one of `queued`, `settling`, `retrying`, `settled`, `failed`, or `cancelled`.
- `lines`: list of `{ "vendor_id": string, "amount_cents": integer }`.
- `attempts`: integer retry count.
- `max_attempts`: integer retry limit.
- `metadata`: object for retry/failure information and unrelated business fields.

Each lock row has:

- `payout_id`: owning payout id.
- `vendor_id`: reserved vendor id.
- `amount_cents`: reserved quantity.
- `owner`: system that owns the lock, such as `payout_worker`, `legacy_gateway`, or `finance_override`.
- `state`: `active` or `stale`.
- `source_attempt`: payout attempt that created or refreshed the row.

Only payouts in `queued`, `settling`, or `retrying` are active and may be processed. Payouts in `settled`, `failed`, or `cancelled` are terminal and must stay unchanged, including their metadata and existing lock rows.

For each active payout, the worker should reserve every requested line if possible. Existing active locks owned by `payout_worker` for the same payout and vendor should be reused when the quantity already matches. Stale same-owner locks or same-owner locks with the wrong quantity should be refreshed in place instead of duplicated. Active same-owner locks for vendors no longer requested by the payout should be removed.

The worker must never overwrite an active lock owned by another owner for the same vendor. Other-owner locks reduce available funds. If an payout cannot be fully reserved because funds is missing or protected by another owner, the payout moves to `retrying` while attempts remain. When attempts are exhausted, the payout moves to `failed`. A conflict retry should record `metadata.retry_reason` as `funds_conflict`; a missing balances retry should record it as `funds_unavailable`.

When an active payout succeeds, its status becomes `settled`, all requested locks are active and owned by `payout_worker`, and stale retry/failure keys are removed from metadata: `retry_reason`, `last_error`, and `failed_at`. Unrelated metadata must be preserved.

Retry attempts should increase only when the current run cannot fully reserve the payout. Successful recovery must not increase attempts. Re-running the worker on an already repaired document must be stable: no duplicate locks, no inflated counters, and no changes to terminal payouts.

Keep your fix inside `/app`. Do not modify tests, logs, or reward files.

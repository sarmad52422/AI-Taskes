An equipment rental shop keeps a persisted pickup allocation document in `/app/pickup_reservations.json`. The worker at `/app/pickup_worker.py` should allocate physical units for customer bookings before the counter team starts pickup staging, but the current implementation rewrites terminal bookings, duplicates allocations, leaves stale retry metadata behind, and can overwrite rows created by the repair bay or fleet import.

Run the worker with:

```bash
python /app/pickup_worker.py
```

The worker must load `/app/pickup_reservations.json`, process eligible bookings, save the same JSON file, and return the persisted document from `run()`.

The JSON document has these top-level keys:

- `inventory`: object keyed by equipment id. Each equipment record has `on_hand` as an integer.
- `bookings`: list of booking objects.
- `reservations`: list of pickup allocation rows.
- `stats`: object with counters such as `reservations_created`, `reservations_refreshed`, `conflicts`, and `failures`.

Each booking has:

- `id`: stable booking id.
- `status`: one of `queued`, `allocating`, `retrying`, `allocated`, `failed`, or `cancelled`.
- `items`: list of `{ "equipment_id": string, "units": integer }`.
- `attempts`: integer retry count.
- `max_attempts`: integer retry limit.
- `metadata`: object for retry/failure information and unrelated rental fields.

Each reservation row has:

- `booking_id`: owning booking id.
- `equipment_id`: allocated equipment id.
- `units`: allocated unit count.
- `owner`: system that owns the allocation, such as `pickup_worker`, `counter_agent`, or `fleet_import`.
- `state`: `active` or `stale`.
- `source_attempt`: booking attempt that created or refreshed the row.

Only bookings in `queued`, `allocating`, or `retrying` are active and may be processed. Bookings in `allocated`, `failed`, or `cancelled` are terminal and must stay unchanged, including their metadata and existing reservation rows.

For each active booking, the worker should allocate every requested equipment type if possible. Existing active reservations owned by `pickup_worker` for the same booking and equipment should be reused when the unit count already matches. Stale same-owner rows or same-owner rows with the wrong unit count should be refreshed in place instead of duplicated. Active same-owner rows for equipment no longer requested by the booking should be removed.

The worker must never overwrite an active reservation owned by another owner for the same equipment. Other-owner reservations reduce available inventory. If a booking cannot be fully allocated because stock is missing or protected by another owner, the booking moves to `retrying` while attempts remain. When attempts are exhausted, the booking moves to `failed`. A conflict retry should record `metadata.retry_reason` as `unit_conflict`; a missing inventory retry should record it as `unit_unavailable`.

When an active booking succeeds, its status becomes `allocated`, all requested reservations are active and owned by `pickup_worker`, and stale retry/failure keys are removed from metadata: `retry_reason`, `last_error`, and `failed_at`. Unrelated metadata must be preserved.

Retry attempts should increase only when the current run cannot fully allocate the booking. Successful recovery must not increase attempts. Re-running the worker on an already repaired document must be stable: no duplicate reservations, no inflated counters, and no changes to terminal bookings.

Keep your fix inside `/app`. Do not modify tests, logs, or reward files.

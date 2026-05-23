The subscription proration worker has started producing inconsistent billing state after rerunning adjustment batches.

Finance support has reported a few recurring problems:

- customer billing periods losing balance even when no new adjustment was requested
- duplicate ledger entry ids appearing after retry recovery
- retrying adjustments still carrying failure state after a successful post
- repeated worker runs changing records that should already be stable

Run the worker with:

```bash
python /app/proration_worker.py
```

The worker reads and writes `/app/events.json`.

The file contains three top-level sections:

- `accounts`
- `ledger`
- `jobs`

Each job contains:

- `adjustment_id`
- `status`
- `retries`
- `customer_id`
- `period`
- `amount_cents`
- `ledger_entry_ids`
- `metadata`

Recognized statuses:

- `pending`
- `retrying`
- `posted`
- `completed`
- `voided`
- `failed`

Pending adjustments should post when enough remaining balance exists for the requested billing period.

Retrying adjustments should be attempted again.

`completed`, `voided`, and `failed` adjustments are historical terminal records and must remain unchanged.

Adjustments successfully handled by this worker should move to `posted`. Historical `completed` records should not be newly written during processing.

If only part of the requested amount is available for a billing period, the adjustment should move into `retrying` before eventually becoming `failed`.

If retries have already been exhausted and enough balance still does not exist, the adjustment should become `failed`.

Some retrying adjustments may already have part of their amount reflected in the ledger. Recovery should continue from the existing posted amount instead of starting over.

If part of an adjustment already exists:

- top up the existing ledger entry
- avoid creating another entry for the same adjustment
- keep `ledger_entry_ids` normalized
- avoid deducting balance twice

When retrying work succeeds:

- stale retry or failure state should be cleared
- unrelated metadata should remain intact

Repeated worker runs should not:

- duplicate ledger entry ids
- deduct the same period balance twice
- rewrite terminal records
- create empty ledger account containers or empty `entries` objects for adjustments that were never posted

The final state of `/app/events.json` should keep billing periods, ledger entries, and adjustment records aligned with the work that was actually posted.

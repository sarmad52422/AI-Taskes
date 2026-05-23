The warehouse reservation worker is drifting reservation state when the same batch is run more than once. Operators have seen inventory counts change without new demand, duplicate reservation IDs on recovered jobs, and retry records that still carry old failure details after stock becomes available.

The worker reads and writes `/app/jobs.json`. That file contains an `inventory` object and a `jobs` list. Each job includes:

- `reservation_id`
- `status`
- `retries`
- `item_id`
- `quantity`
- `warehouse`
- `reservation_ids`
- `metadata`

Statuses can be `pending`, `retrying`, `reserved`, `released`, `failed`, or `completed`.

Run the worker with:

```bash
python /app/reservation_worker.py
```

The reservation lifecycle should be stable across repeated worker runs. Pending jobs should reserve stock when enough inventory exists, and retrying jobs should be attempted again. Completed and failed jobs are terminal records and must remain unchanged. A job that cannot reserve because only part of the requested quantity is available should move into retrying before it is failed. Once the retry limit is exhausted, an unavailable reservation should become failed.

When a retrying reservation eventually succeeds, it should no longer carry stale retry or failure state, but unrelated metadata should be preserved. Re-running the worker must not duplicate `reservation_ids` or deduct stock more than once for the same reservation. If inventory already contains a hold for part of the same reservation, recovery should account for that existing hold instead of starting from zero. Some retrying reservations may already contain a partial hold. When they recover, top up the existing hold and keep the reservation id list normalized rather than appending another copy of the same id.

Inventory availability and reserved holds should stay consistent with the reservation records in `/app/jobs.json`.
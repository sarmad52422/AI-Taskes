The reservation worker has started drifting state when the same batch is run more than once. We have seen inventory counts move without new work, duplicate reservation IDs on records, and retry records that still look failed after they later reserve successfully.

The code lives in `/app`:

- `/app/reservation_worker.py`
- `/app/inventory_store.py`
- `/app/reservation_rules.py`
- `/app/jobs.json`
- `/app/inventory.json`

Run the worker with:

```bash
python /app/reservation_worker.py
```

Fix the reservation lifecycle so the persisted job and inventory JSON files remain consistent across repeated runs.

Expected behavior:

- `pending` jobs reserve stock when enough inventory is available.
- `retrying` jobs are processed again.
- `completed` jobs are historical records and must not be changed.
- `failed` jobs are terminal and must not be changed.
- Re-running the worker must not duplicate `reservation_ids`.
- Re-running the worker must not reserve the same quantity twice.
- If a retrying job succeeds, stale retry metadata and stale failure state should be removed.
- A partial stock shortage should leave the job retrying until the retry limit is exhausted.
- Jobs that have exhausted the retry limit should become failed when they still cannot reserve.
- Inventory totals and reservation holds should stay aligned with the jobs that were actually processed.

Keep the fix focused on the runtime files under `/app`. Do not change the tests.

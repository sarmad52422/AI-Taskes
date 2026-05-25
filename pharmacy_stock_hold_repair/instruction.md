A pharmacy order service keeps a persisted stock-hold document in `/app/stock_holds.json`. The nightly worker at `/app/hold_worker.py` is supposed to reserve shelf stock for customer orders before packing starts, but a recent change made it rewrite terminal orders, duplicate holds, leave stale retry metadata behind, and overwrite holds created by other systems.

Run the worker with:

```bash
python /app/hold_worker.py
```

The worker must load `/app/stock_holds.json`, process eligible orders, save the same JSON file, and return the persisted document from `run()`.

The JSON document has these top-level keys:

- `inventory`: object keyed by medicine id. Each medicine has `on_hand` as an integer.
- `orders`: list of order objects.
- `holds`: list of hold rows.
- `stats`: object with counters such as `holds_created`, `holds_refreshed`, `conflicts`, and `failures`.

Each order has:

- `id`: stable order id.
- `status`: one of `queued`, `holding`, `retrying`, `held`, `failed`, or `cancelled`.
- `items`: list of `{ "medicine_id": string, "qty": integer }`.
- `attempts`: integer retry count.
- `max_attempts`: integer retry limit.
- `metadata`: object for retry/failure information and unrelated business fields.

Each hold row has:

- `order_id`: owning order id.
- `medicine_id`: reserved medicine id.
- `qty`: reserved quantity.
- `owner`: system that owns the hold, such as `hold_worker`, `legacy_import`, or `manual_counter`.
- `state`: `active` or `stale`.
- `source_attempt`: order attempt that created or refreshed the row.

Only orders in `queued`, `holding`, or `retrying` are active and may be processed. Orders in `held`, `failed`, or `cancelled` are terminal and must stay unchanged, including their metadata and existing hold rows.

For each active order, the worker should reserve every requested item if possible. Existing active holds owned by `hold_worker` for the same order and medicine should be reused when the quantity already matches. Stale same-owner holds or same-owner holds with the wrong quantity should be refreshed in place instead of duplicated. Active same-owner holds for medicines no longer requested by the order should be removed.

The worker must never overwrite an active hold owned by another owner for the same medicine. Other-owner holds reduce available stock. If an order cannot be fully reserved because stock is missing or protected by another owner, the order moves to `retrying` while attempts remain. When attempts are exhausted, the order moves to `failed`. A conflict retry should record `metadata.retry_reason` as `stock_conflict`; a missing inventory retry should record it as `stock_unavailable`.

When an active order succeeds, its status becomes `held`, all requested holds are active and owned by `hold_worker`, and stale retry/failure keys are removed from metadata: `retry_reason`, `last_error`, and `failed_at`. Unrelated metadata must be preserved.

Retry attempts should increase only when the current run cannot fully reserve the order. Successful recovery must not increase attempts. Re-running the worker on an already repaired document must be stable: no duplicate holds, no inflated counters, and no changes to terminal orders.

Keep your fix inside `/app`. Do not modify tests, logs, or reward files.

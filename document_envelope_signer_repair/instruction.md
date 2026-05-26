A document signing envelope service keeps a persisted signer slot-slot document in `/app/envelope_signers.json`. The nightly worker at `/app/signer_worker.py` is supposed to reserve shelf signer slot for sender envelopes before packing starts, but a recent change made it rewrite terminal envelopes, duplicate slots, leave stale retry metadata behind, and overwrite slots created by other systems.

Run the worker with:

```bash
python /app/signer_worker.py
```

The worker must load `/app/envelope_signers.json`, process eligible envelopes, save the same JSON file, and return the persisted document from `run()`.

The JSON document has these top-level keys:

- `role_capacity`: object keyed by signer id. Each signer has `available_slots` as an integer.
- `envelopes`: list of envelope objects.
- `slots`: list of slot rows.
- `stats`: object with counters such as `slots_created`, `slots_refreshed`, `conflicts`, and `failures`.

Each envelope has:

- `id`: stable envelope id.
- `status`: one of `queued`, `preparing`, `retrying`, `ready`, `failed`, or `cancelled`.
- `signer_requests`: list of `{ "signer_role": string, "slot_count": integer }`.
- `attempts`: integer retry count.
- `max_attempts`: integer retry limit.
- `metadata`: object for retry/failure information and unrelated business fields.

Each slot row has:

- `envelope_id`: owning envelope id.
- `signer_role`: reserved signer id.
- `slot_count`: reserved quantity.
- `owner`: system that owns the slot, such as `signer_worker`, `legacy_envelope_import`, or `legal_override`.
- `state`: `active` or `stale`.
- `source_attempt`: envelope attempt that created or refreshed the row.

Only envelopes in `queued`, `preparing`, or `retrying` are active and may be processed. Envelopes in `ready`, `failed`, or `cancelled` are terminal and must stay unchanged, including their metadata and existing slot rows.

For each active envelope, the worker should reserve every requested signer_request if possible. Existing active slots owned by `signer_worker` for the same envelope and signer should be reused when the quantity already matches. Stale same-owner slots or same-owner slots with the wrong quantity should be refreshed in place instead of duplicated. Active same-owner slots for signers no longer requested by the envelope should be removed.

The worker must never overwrite an active slot owned by another owner for the same signer. Other-owner slots reduce available signer slot. If an envelope cannot be fully reserved because signer slot is missing or protected by another owner, the envelope moves to `retrying` while attempts remain. When attempts are exhausted, the envelope moves to `failed`. A conflict retry should record `metadata.retry_reason` as `signer_slot_conflict`; a missing role_capacity retry should record it as `signer_slot_unavailable`.

When an active envelope succeeds, its status becomes `ready`, all requested slots are active and owned by `signer_worker`, and stale retry/failure keys are removed from metadata: `retry_reason`, `last_error`, and `failed_at`. Unrelated metadata must be preserved.

Retry attempts should increase only when the current run cannot fully reserve the envelope. Successful recovery must not increase attempts. Re-running the worker on an already repaired document must be stable: no duplicate slots, no inflated counters, and no changes to terminal envelopes.

Keep your fix inside `/app`. Do not modify tests, logs, or reward files.

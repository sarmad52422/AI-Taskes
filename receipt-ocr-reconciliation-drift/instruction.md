# Receipt OCR reconciliation drift

The receipt import job has been leaving odd state behind after retries. It is small enough to debug locally, but please treat it like production code: the file on disk is the source of truth and the worker may be run more than once for the same batch.

The worker reads and writes `/app/receipts.json`. A job has a `job_id`, `receipt_id`, `status`, `attempts`, `merchant`, `page_ids`, requested `field_keys`, persisted `field_ids`, and free-form `metadata`. OCR page output lives under `ocr_pages`, and reconciled fields are stored under `reconciliation_index`.

Expected behavior:

- `queued` and `retrying` jobs should be processed.
- `exported`, `needs_review`, and `failed` jobs should be left alone.
- A job reconciles only when every requested field is available from the requested pages and meets the confidence threshold.
- If fields are missing or below threshold, keep the job retryable until attempts are exhausted. Exhausted work becomes `failed`.
- Do not write a partial reconciliation for a failed pass.
- Re-running the worker must not duplicate `field_ids`, inflate `reconciled_count`, or rewrite unchanged field records.
- When a retry finally succeeds, remove stale retry details such as `last_error`, `retry_after`, `partial_fields`, and `failure_reason`. Keep unrelated metadata.
- If an existing field for the same job has stale page checksum or value data, refresh it from the current OCR page.
- If the index already contains a requested field owned by a different job, do not overwrite it. The current job should retry with `reconciliation_conflict`; exhausted conflict retries should fail with the same reason.
- Persisted output and the value returned by the worker should match.

Keep the fix inside the runtime files in `/app`.

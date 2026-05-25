A clinic lab worker has been leaving patient result documents in inconsistent states after sample data arrives late or after another job already owns a protected result row. Fix the runtime code in `/app` so the worker repairs `/app/lab_data.json` correctly when run with:

```bash
python /app/lab_worker.py
```

The JSON document contains `jobs`, `samples`, and `result_rows`. Jobs with status `queued` or `retrying` are active. Jobs with status `completed`, `failed`, or `cancelled` are terminal and must not be changed. Each active job has `job_id`, `patient_id`, `required_tests`, `attempts`, `max_attempts`, and optional `metadata`. A result row has `patient_id`, `test_code`, `value`, `unit`, `source_version`, and `owner_job_id`.

For each active job, build one result row for every required test from the matching sample. Existing same-owner rows may be reused when their `source_version` and value match the current sample. Existing same-owner rows that are stale must be refreshed. Same-owner rows for tests no longer required by that job must be removed. Rows owned by another job are protected when the other owner is active or terminal completed; they must not be overwritten.

If any required sample is missing, the job moves to `retrying` and records `metadata.retry_reason = "missing_sample"` unless it has exhausted retries, in which case it becomes `failed`. Missing samples must not create partial result rows. If a protected row conflict exists, the job moves to `retrying` with `metadata.retry_reason = "protected_result_conflict"`, or to `failed` with the same reason when retries are exhausted. Conflict handling must leave protected rows unchanged.

On success, the job becomes `completed`, `attempts` must not be inflated by repeated runs, stale retry fields such as `retry_reason` and `last_error` must be removed, and unrelated metadata must be preserved. The worker should save the repaired JSON and return the same document it persisted. Keep the fix inside `/app` runtime files only.

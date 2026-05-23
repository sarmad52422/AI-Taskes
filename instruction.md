The session summary worker has started producing unreliable queue state after a recent change to the local model client. It reads jobs from `/app/jobs.json`, sends open sessions to the model client, and stores normalized review fields back on the same job records.

Each job has an `id`, `status`, `retries`, and `events`. Completed jobs should keep `summary`, `skill`, and `confidence`; older completed records may also have metadata such as `reviewed_by`. Status values in this task are `pending`, `retrying`, `completed`, and `failed`.

A model response may be strict JSON or a readable text response. Either form is usable only when it gives a summary, a skill, and a numeric confidence. The stored field names should be `summary`, `skill`, and `confidence` even when the model uses nearby names. A job should become completed only when confidence is at least `0.7`.

Responses that are missing required signal, or have confidence below `0.7`, should not leave partial summary fields on the job. They should move through the retry path. The retry limit is `2`; a job already on its second failed attempt should become `failed`.

Already completed jobs should not be rewritten when the worker runs again. Event history and existing completed-job metadata should remain stable. Keep the change in the application files under `/app` and do not edit the tests or verifier output.

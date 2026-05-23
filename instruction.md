We have a reliability bug in the session summary worker.

The worker consumes queued session-summary jobs from `/app/jobs.json`, asks the model client for a structured response, and stores the summary back on the job. The relevant application code is in `/app/queue_manager.py` and `/app/pipeline.py`. In production the model is usually cooperative, but it sometimes returns a readable object-like response that is not strict JSON. Those responses are currently enough to knock over a worker run, and the queue state after a retry has been hard to reason about.

Jobs in `/app/jobs.json` are JSON objects with this shape:

- `id`: string identifier for the job.
- `status`: one of `pending`, `retrying`, `completed`, or `failed`.
- `retries`: integer count of unusable model responses already recorded for the job.
- `events`: list of event objects. Each event has at least `kind` and `text` string fields.
- `summary`: optional string produced by the worker. It is required and non-empty when `status` is `completed`.
- `skill`: optional string skill label produced by the model when available.

Please make this path resilient. A pending or retrying job with a readable summary should finish with a useful, non-empty string summary even when the model response is slightly malformed. For example, a response shaped like `{summary: Player improved timing, skill: patience}` should be accepted even though it is not strict JSON.

A response that truly cannot be used should follow the retry policy and must not be marked successful. Use `MAX_RETRIES = 2`: after the first unusable response the job should move from `pending` to `retrying` with `retries == 1`, and after the second unusable response it should move to `failed` with `retries == 2`.

Existing completed records must stay stable when the worker is run again; downstream consumers trust those summaries and the original event history. Completed jobs should not be claimed for additional processing, and no run should mutate the original `events` lists. Every job whose status is `completed` must have a non-empty string `summary`.

The fake model in this task reproduces the responses we have seen, so there is no external service to call. Keep the fix in the application code and leave the verifier and oracle files alone.

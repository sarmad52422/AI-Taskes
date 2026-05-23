The embedding indexer has started drifting state when the same document batch is run more than once. Search operators have seen duplicate embedding ids on recovered jobs, cached vectors rewritten without document changes, and retry records that still look failed after all chunks are available.

The worker reads and writes `/app/jobs.json`. That file contains a `documents` object, an `embedding_cache` object, and a `jobs` list. Each job includes:

- `job_id`
- `status`
- `retries`
- `document_id`
- `chunks`
- `model`
- `embedding_ids`
- `metadata`

Statuses can be `pending`, `retrying`, `indexed`, `completed`, or `failed`.

Run the worker with:

```bash
python /app/embedding_worker.py
```

The indexing lifecycle should be stable across repeated worker runs. Pending jobs should embed every requested chunk when the source text exists, and retrying jobs should be attempted again. Completed and failed jobs are terminal records and must remain unchanged. If only part of the requested chunk set is available, the job should move into retrying before it is failed. Once the retry limit is exhausted, an unavailable job should become failed.

When a retrying job eventually succeeds, it should no longer carry stale retry or failure state, but unrelated metadata should be preserved. Re-running the worker must not duplicate `embedding_ids` or rewrite cached vectors for the same job and model. If the cache already contains embeddings for part of the same job, recovery should top up the missing chunk embeddings and keep the id list normalized rather than starting the job over.

The cache entries in `/app/jobs.json` should stay aligned with the job records that were actually processed.

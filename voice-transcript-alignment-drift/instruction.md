The voice transcript alignment worker is drifting alignment state when the same audio batch is run more than once. ML data ops has seen aligned segment counts change without new audio, duplicate alignment ids after retry recovery, and retrying jobs that still carry failure state after the transcript segments are aligned.

The worker reads and writes `/app/alignments.json`. That file contains an `audio_files` object, an `alignment_cache` object, and a `jobs` list. Each job includes:

- `alignment_id`
- `status`
- `retries`
- `audio_id`
- `speaker`
- `segment_ids`
- `alignment_ids`
- `metadata`

Statuses can be `pending`, `retrying`, `aligned`, `completed`, `discarded`, or `failed`.

Run the worker with:

```bash
python /app/alignment_worker.py
```

The alignment lifecycle should be stable across repeated worker runs. Pending jobs should align every requested segment when the audio transcript segments exist, and retrying jobs should be attempted again. Completed, discarded, and failed jobs are historical terminal records and must remain unchanged. Jobs successfully handled by this worker should move to `aligned`.

If only part of the requested segment set is available, the job should move into `retrying` before it is failed. Once the retry limit is exhausted, an unavailable alignment should become failed.

Some retrying jobs may already have part of their segment alignment cached. Recovery should continue from the existing aligned segments instead of starting over. Do not create another alignment for the same segment and job, duplicate `alignment_ids`, keep ids for segments no longer requested, change the requested segment order, or increase the aligned count twice for the same segment.

When retrying work succeeds, stale retry and failure state should be removed from the job while unrelated metadata remains intact. Jobs that never align segments should not create empty alignment cache containers.

A cached segment owned by a different alignment job for the same audio and speaker should not be overwritten by a new job; the new job should retry with `alignment_conflict`, and exhausted conflict retries should fail with that same reason. If a cached segment belongs to the same job but no longer matches the current transcript timing, refresh that cached segment while preserving already-correct segment records.

The final `/app/alignments.json` should keep transcript segments, cached alignments, aligned counts, and job records aligned with the segments that were actually processed.

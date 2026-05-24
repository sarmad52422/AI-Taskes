The feature rollout assignment worker is drifting cohort state when the same rollout batch is run more than once. Product operations has seen audience counts change without a new rollout, duplicate assignment ids after retry recovery, and retrying jobs that still look failed after the cohort is assigned.

The worker reads and writes `/app/rollouts.json`. That file contains a `segments` object, an `assignments` object, and a `jobs` list. Each job includes:

- `rollout_id`
- `status`
- `retries`
- `feature_key`
- `segment`
- `target_count`
- `assignment_ids`
- `metadata`

Statuses can be `pending`, `retrying`, `active`, `completed`, `cancelled`, or `failed`.

Run the worker with:

```bash
python /app/rollout_worker.py
```

The rollout lifecycle should be stable across repeated worker runs. Pending jobs should assign the requested number of eligible users when enough audience remains, and retrying jobs should be attempted again. Completed, cancelled, and failed jobs are historical terminal records and must remain unchanged. Jobs successfully handled by this worker should move to `active`.

If only part of the requested audience is available, the job should move into `retrying` before it is failed. Once the retry limit is exhausted, an unavailable rollout should become failed.

Some retrying jobs may already have part of their cohort assigned. Recovery should continue from the existing assignments instead of starting over. Do not create another assignment for the same user and rollout, duplicate `assignment_ids`, or increase the assigned count twice for the same assignment.

When retrying work succeeds, stale retry and failure state should be removed from the job while unrelated metadata remains intact. Jobs that never assign users should not create empty assignment containers.

The final `/app/rollouts.json` should keep segment eligibility, assignment records, assigned counts, and job records aligned with the users that were actually assigned.

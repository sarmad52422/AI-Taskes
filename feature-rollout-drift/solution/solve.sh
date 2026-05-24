#!/bin/bash
set -euo pipefail

cat > /app/rollout_store.py <<'PY'
import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "rollouts.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "rollouts.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def eligible_users(document, segment):
    record = document.setdefault("segments", {}).setdefault(segment, {})
    return list(record.setdefault("eligible_users", []))


def assignment_record(document, feature_key, segment):
    feature = document.setdefault("assignments", {}).setdefault(feature_key, {})
    return feature.setdefault(segment, {"assigned_count": 0, "users": {}})


def existing_record(document, feature_key, segment):
    return document.get("assignments", {}).get(feature_key, {}).get(segment, {})


def assignment_id(job, user_id):
    return f"{job['rollout_id']}:{user_id}"


def existing_users_for_job(document, job):
    users = existing_record(document, job["feature_key"], job["segment"]).get("users", {})
    return [
        user_id
        for user_id, assignment in users.items()
        if assignment.get("rollout_id") == job["rollout_id"]
    ]


def users_assigned_to_other_rollouts(document, job):
    users = existing_record(document, job["feature_key"], job["segment"]).get("users", {})
    return {
        user_id
        for user_id, assignment in users.items()
        if assignment.get("rollout_id") != job["rollout_id"]
    }


def available_users(document, job):
    blocked = users_assigned_to_other_rollouts(document, job)
    existing = set(existing_users_for_job(document, job))
    return [
        user_id
        for user_id in eligible_users(document, job["segment"])
        if user_id not in blocked and user_id not in existing
    ]


def normalize_assignment_ids(job, user_ids):
    expected = [assignment_id(job, user_id) for user_id in user_ids]
    current = []
    for value in job.setdefault("assignment_ids", []):
        if value in expected and value not in current:
            current.append(value)
    for value in expected:
        if value not in current:
            current.append(value)
    job["assignment_ids"] = current


def assign_users(document, job):
    target = int(job["target_count"])
    existing = existing_users_for_job(document, job)
    if len(existing) >= target:
        normalize_assignment_ids(job, existing[:target])
        return True

    needed = target - len(existing)
    available = available_users(document, job)
    if len(available) < needed:
        return False

    selected = existing + available[:needed]
    record = assignment_record(document, job["feature_key"], job["segment"])
    users = record.setdefault("users", {})
    for user_id in available[:needed]:
        users[user_id] = {
            "assignment_id": assignment_id(job, user_id),
            "rollout_id": job["rollout_id"],
            "status": "active",
            "write_count": 1,
        }
    record["assigned_count"] = len(users)
    normalize_assignment_ids(job, selected)
    return True
PY

cat > /app/rollout_rules.py <<'PY'
MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying"}
STALE_RETRY_METADATA = {"last_error", "retry_after", "retry_started_at", "next_retry_at"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, available):
    target = int(job.get("target_count", 0))
    if available <= 0:
        return "audience_exhausted"
    if available < target:
        return "partial_audience"
    return "unavailable"


def should_retry(job, reason):
    return int(job.get("retries", 0)) < MAX_RETRIES


def mark_retrying(job, reason):
    job["status"] = "retrying"
    job["retries"] = int(job.get("retries", 0)) + 1
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["retry_after"] = "next-run"
    metadata["last_attempt_status"] = "retrying"


def mark_failed(job, reason):
    job["status"] = "failed"
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["last_attempt_status"] = "failed"


def mark_active(job):
    job["status"] = "active"
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    for key in STALE_RETRY_METADATA:
        metadata.pop(key, None)
    metadata["last_attempt_status"] = "active"
    metadata["activated_at"] = "worker-run"
PY

cat > /app/rollout_worker.py <<'PY'
import os
from pathlib import Path

import rollout_rules
import rollout_store


def process_job(document, job):
    if not rollout_rules.should_process(job):
        return job

    if rollout_store.assign_users(document, job):
        rollout_rules.mark_active(job)
        return job

    available = len(rollout_store.available_users(document, job))
    reason = rollout_rules.failure_reason(job, available)
    if rollout_rules.should_retry(job, reason):
        rollout_rules.mark_retrying(job, reason)
    else:
        rollout_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = rollout_store.load_document(app_dir)
    for job in rollout_store.jobs(document):
        process_job(document, job)
    rollout_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("ROLLOUT_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()
PY

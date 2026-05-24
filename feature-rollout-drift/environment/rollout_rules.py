MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying", "active", "completed"}


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
    if reason == "partial_audience":
        return False
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
    metadata = job.setdefault("metadata", {})
    metadata["last_attempt_status"] = "active"
    metadata["activated_at"] = "worker-run"

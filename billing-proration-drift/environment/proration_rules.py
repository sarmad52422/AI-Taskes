MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying", "posted", "completed"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, remaining):
    amount = int(job.get("amount_cents", 0))
    if remaining <= 0:
        return "period_unavailable"
    if remaining < amount:
        return "partial_period_balance"
    return "unavailable"


def should_retry(job, reason):
    if reason == "partial_period_balance":
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


def mark_posted(job):
    job["status"] = "posted"
    metadata = job.setdefault("metadata", {})
    metadata["last_attempt_status"] = "posted"
    metadata["posted_at"] = "worker-run"

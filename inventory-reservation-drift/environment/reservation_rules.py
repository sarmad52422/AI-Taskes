MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying", "reserved", "completed"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, available):
    quantity = int(job.get("quantity", 0))
    if available <= 0:
        return "out_of_stock"
    if available < quantity:
        return "partial_stock"
    return "unavailable"


def should_retry(job, reason):
    if reason == "partial_stock":
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


def mark_reserved(job):
    job["status"] = "reserved"
    metadata = job.setdefault("metadata", {})
    metadata["last_attempt_status"] = "reserved"
    metadata["reserved_at"] = "worker-run"

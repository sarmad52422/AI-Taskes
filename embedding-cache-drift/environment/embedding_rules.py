MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying", "indexed", "completed"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, missing):
    requested = len(job.get("chunks", []))
    if len(missing) == requested:
        return "document_unavailable"
    return "partial_document"


def should_retry(job, reason):
    if reason == "partial_document":
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


def mark_indexed(job):
    job["status"] = "indexed"
    metadata = job.setdefault("metadata", {})
    metadata["last_attempt_status"] = "indexed"
    metadata["indexed_at"] = "worker-run"

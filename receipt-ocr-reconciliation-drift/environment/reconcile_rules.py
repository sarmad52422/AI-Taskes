MAX_ATTEMPTS = 3
MIN_CONFIDENCE = 0.8


TERMINAL_STATUSES = {"exported", "failed", "needs_review"}


def is_terminal(job):
    return job.get("status") in TERMINAL_STATUSES


def is_workable(job):
    return job.get("status") in {"queued", "retrying"}


def field_is_usable(field):
    return field is not None and field.get("confidence", 0) >= MIN_CONFIDENCE


def failure_reason(missing, low_confidence):
    if low_confidence:
        return "low_confidence_fields"
    return "missing_ocr_fields"


def can_retry(job):
    return job.get("attempts", 0) < MAX_ATTEMPTS


def retry_after(job):
    return f"after-attempt-{job.get('attempts', 0) + 1}"

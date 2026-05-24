from ocr_store import field_id_for, find_field, load_document, save_document, write_field
from reconcile_rules import can_retry, failure_reason, field_is_usable, is_terminal, is_workable, retry_after


def mark_retry(job, reason, missing):
    job["attempts"] = job.get("attempts", 0) + 1
    if can_retry(job):
        job["status"] = "retrying"
    else:
        job["status"] = "failed"
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["retry_after"] = retry_after(job)
    metadata["partial_fields"] = missing


def mark_reconciled(job, field_ids):
    job["status"] = "reconciled"
    job["field_ids"] = job.get("field_ids", []) + field_ids
    job["failure_reason"] = None
    job.setdefault("metadata", {})


def process_job(document, job):
    if is_terminal(job) or not is_workable(job):
        return job

    missing = []
    low_confidence = []
    usable = {}
    for field_key in job.get("field_keys", []):
        field = find_field(document, job, field_key)
        if field is None:
            missing.append(field_key)
        elif not field_is_usable(field):
            low_confidence.append(field_key)
        else:
            usable[field_key] = field

    if missing or low_confidence:
        for field_key, field in usable.items():
            write_field(document, job, field_key, field)
        mark_retry(job, failure_reason(missing, low_confidence), missing + low_confidence)
        return job

    field_ids = []
    for field_key in job.get("field_keys", []):
        record = write_field(document, job, field_key, usable[field_key])
        field_ids.append(record["field_id"])
    mark_reconciled(job, field_ids)
    return job


def run(path="/app/receipts.json"):
    document = load_document(path)
    for job in document.get("jobs", []):
        process_job(document, job)
    save_document(document, path)
    return document


if __name__ == "__main__":
    run()

#!/bin/bash
set -e

cat > /app/ocr_store.py <<'PY'
import copy
import json
from pathlib import Path


DATA_PATH = Path("/app/receipts.json")


def load_document(path=DATA_PATH):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def receipt_bucket(document, receipt_id):
    index = document.setdefault("reconciliation_index", {})
    bucket = index.setdefault(receipt_id, {})
    bucket.setdefault("fields", {})
    bucket.setdefault("reconciled_count", len(bucket["fields"]))
    return bucket


def ocr_pages_for(document, receipt_id):
    return document.get("ocr_pages", {}).get(receipt_id, {})


def find_field(document, job, field_key):
    pages = ocr_pages_for(document, job["receipt_id"])
    for page_id in job.get("page_ids", []):
        page = pages.get(page_id)
        if not page:
            continue
        for field in page.get("fields", []):
            if field.get("key") == field_key:
                found = copy.deepcopy(field)
                found["page_id"] = page_id
                found["checksum"] = page.get("checksum")
                return found
    return None


def field_id_for(job, field_key):
    return f"{job['job_id']}:{field_key}"


def cached_field(document, job, field_key):
    bucket = receipt_bucket(document, job["receipt_id"])
    return bucket["fields"].get(field_key)


def record_matches_field(record, field):
    return (
        record.get("source_checksum") == field.get("checksum")
        and record.get("raw_value") == field.get("raw_value", "")
        and record.get("normalized_value") == field.get("normalized_value", field.get("raw_value", ""))
        and record.get("page_id") == field.get("page_id")
    )


def has_conflict(document, job):
    for field_key in job.get("field_keys", []):
        record = cached_field(document, job, field_key)
        if record and record.get("job_id") != job.get("job_id"):
            return True
    return False


def write_field(document, job, field_key, field):
    bucket = receipt_bucket(document, job["receipt_id"])
    previous = bucket["fields"].get(field_key)
    if previous and previous.get("job_id") == job.get("job_id") and record_matches_field(previous, field):
        return previous, False
    write_count = previous.get("write_count", 0) + 1 if previous else 1
    record = {
        "field_id": field_id_for(job, field_key),
        "job_id": job["job_id"],
        "page_id": field["page_id"],
        "source_checksum": field["checksum"],
        "raw_value": field.get("raw_value", ""),
        "normalized_value": field.get("normalized_value", field.get("raw_value", "")),
        "confidence": field.get("confidence", 0),
        "write_count": write_count,
    }
    bucket["fields"][field_key] = record
    bucket["reconciled_count"] = len(bucket["fields"])
    return record, True


def normalize_count(document, receipt_id):
    bucket = receipt_bucket(document, receipt_id)
    bucket["reconciled_count"] = len(bucket["fields"])
PY

cat > /app/reconcile_rules.py <<'PY'
MAX_ATTEMPTS = 3
MIN_CONFIDENCE = 0.8


TERMINAL_STATUSES = {"exported", "failed", "needs_review", "reconciled"}


def is_terminal(job):
    return job.get("status") in TERMINAL_STATUSES


def is_workable(job):
    return job.get("status") in {"queued", "retrying"}


def field_is_usable(field):
    return field is not None and field.get("confidence", 0) >= MIN_CONFIDENCE


def failure_reason(missing, low_confidence, conflict=False):
    if conflict:
        return "reconciliation_conflict"
    if low_confidence:
        return "low_confidence_fields"
    return "missing_ocr_fields"


def retries_exhausted(job):
    return job.get("attempts", 0) >= MAX_ATTEMPTS


def retry_after(job):
    return f"after-attempt-{job.get('attempts', 0)}"
PY

cat > /app/reconcile_worker.py <<'PY'
from ocr_store import field_id_for, find_field, has_conflict, load_document, normalize_count, save_document, write_field
from reconcile_rules import failure_reason, field_is_usable, is_terminal, is_workable, retries_exhausted, retry_after


def mark_retry_or_failed(job, reason, partial_fields):
    if not retries_exhausted(job):
        job["attempts"] = job.get("attempts", 0) + 1
    job["status"] = "failed" if retries_exhausted(job) else "retrying"
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["retry_after"] = retry_after(job)
    metadata["partial_fields"] = partial_fields


def mark_reconciled(job):
    job["status"] = "reconciled"
    job["field_ids"] = [field_id_for(job, key) for key in job.get("field_keys", [])]
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    metadata.pop("last_error", None)
    metadata.pop("retry_after", None)
    metadata.pop("partial_fields", None)


def process_job(document, job):
    if is_terminal(job) or not is_workable(job):
        return job

    if has_conflict(document, job):
        mark_retry_or_failed(job, failure_reason([], [], conflict=True), job.get("field_keys", []))
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
        mark_retry_or_failed(job, failure_reason(missing, low_confidence), missing + low_confidence)
        return job

    for field_key in job.get("field_keys", []):
        write_field(document, job, field_key, usable[field_key])
    normalize_count(document, job["receipt_id"])
    mark_reconciled(job)
    return job


def run(path="/app/receipts.json"):
    document = load_document(path)
    for job in document.get("jobs", []):
        process_job(document, job)
    save_document(document, path)
    return document


if __name__ == "__main__":
    run()
PY

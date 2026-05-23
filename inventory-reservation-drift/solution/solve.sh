#!/bin/bash
set -euo pipefail

cat > /app/inventory_store.py <<'PY'
import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "jobs.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "jobs.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def inventory(document):
    return document.setdefault("inventory", {})


def item_record(document, warehouse, item_id):
    warehouse_items = inventory(document).setdefault(warehouse, {})
    return warehouse_items.setdefault(item_id, {"available": 0, "reserved": {}})


def available_quantity(document, job):
    item = item_record(document, job["warehouse"], job["item_id"])
    return int(item.get("available", 0))


def set_reservation_id(job):
    reservation_id = job["reservation_id"]
    existing = []
    for current in job.setdefault("reservation_ids", []):
        if current not in existing:
            existing.append(current)
    if reservation_id not in existing:
        existing.append(reservation_id)
    job["reservation_ids"] = existing


def reserve_stock(document, job):
    item = item_record(document, job["warehouse"], job["item_id"])
    quantity = int(job["quantity"])
    reservation_id = job["reservation_id"]
    reserved = item.setdefault("reserved", {})
    already_reserved = int(reserved.get(reservation_id, 0))
    if already_reserved >= quantity:
        reserved[reservation_id] = already_reserved
        set_reservation_id(job)
        return True

    needed = quantity - already_reserved
    available = int(item.get("available", 0))
    if available < needed:
        return False

    item["available"] = available - needed
    reserved[reservation_id] = quantity
    set_reservation_id(job)
    return True
PY

cat > /app/reservation_rules.py <<'PY'
MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying"}
STALE_RETRY_METADATA = {"last_error", "retry_after", "retry_started_at", "next_retry_at"}


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
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    for key in STALE_RETRY_METADATA:
        metadata.pop(key, None)
    metadata["last_attempt_status"] = "reserved"
    metadata["reserved_at"] = "worker-run"
PY

cat > /app/reservation_worker.py <<'PY'
import os
from pathlib import Path

import inventory_store
import reservation_rules


def process_job(document, job):
    if not reservation_rules.should_process(job):
        return job

    if inventory_store.reserve_stock(document, job):
        reservation_rules.mark_reserved(job)
        return job

    available = inventory_store.available_quantity(document, job)
    reason = reservation_rules.failure_reason(job, available)
    if reservation_rules.should_retry(job, reason):
        reservation_rules.mark_retrying(job, reason)
    else:
        reservation_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = inventory_store.load_document(app_dir)
    for job in inventory_store.jobs(document):
        process_job(document, job)
    inventory_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("RESERVATION_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()
PY

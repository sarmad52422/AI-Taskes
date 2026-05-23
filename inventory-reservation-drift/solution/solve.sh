#!/bin/bash
set -euo pipefail

python - <<'PY'
from pathlib import Path

Path("/app/inventory_store.py").write_text('''import json
from pathlib import Path


def load_json(path):
    with Path(path).open() as handle:
        return json.load(handle)


def save_json(path, payload):
    with Path(path).open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\\n")


def read_jobs(app_dir):
    return load_json(Path(app_dir) / "jobs.json")


def write_jobs(app_dir, jobs):
    save_json(Path(app_dir) / "jobs.json", jobs)


def read_inventory(app_dir):
    return load_json(Path(app_dir) / "inventory.json")


def write_inventory(app_dir, inventory):
    save_json(Path(app_dir) / "inventory.json", inventory)


def item_record(inventory, warehouse, item_id):
    warehouse_items = inventory.setdefault(warehouse, {})
    return warehouse_items.setdefault(item_id, {"available": 0, "reserved": {}})


def available_quantity(inventory, job):
    item = item_record(inventory, job["warehouse"], job["item_id"])
    return int(item.get("available", 0))


def set_job_reservation_id(job):
    reservation_id = job["reservation_id"]
    seen = []
    for current in job.setdefault("reservation_ids", []):
        if current not in seen:
            seen.append(current)
    if reservation_id not in seen:
        seen.append(reservation_id)
    job["reservation_ids"] = seen


def reserve_stock(inventory, job):
    item = item_record(inventory, job["warehouse"], job["item_id"])
    quantity = int(job["quantity"])
    reservation_id = job["reservation_id"]
    reserved = item.setdefault("reserved", {})
    already_reserved = int(reserved.get(reservation_id, 0))
    if already_reserved >= quantity:
        reserved[reservation_id] = already_reserved
        set_job_reservation_id(job)
        return True

    needed = quantity - already_reserved
    available = int(item.get("available", 0))
    if available < needed:
        return False

    item["available"] = available - needed
    reserved[reservation_id] = quantity
    set_job_reservation_id(job)
    return True
''')

Path("/app/reservation_rules.py").write_text('''MAX_RETRIES = 3

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
''')
PY

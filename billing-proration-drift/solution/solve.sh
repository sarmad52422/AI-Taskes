#!/bin/bash
set -euo pipefail

cat > /app/ledger_store.py <<'PY'
import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "events.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "events.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def period_record(document, customer_id, period):
    account = document.setdefault("accounts", {}).setdefault(customer_id, {})
    periods = account.setdefault("periods", {})
    return periods.setdefault(period, {"open": True, "remaining_cents": 0})


def ledger_record(document, customer_id):
    return document.setdefault("ledger", {}).setdefault(customer_id, {"entries": {}})


def remaining_cents(document, job):
    period = period_record(document, job["customer_id"], job["period"])
    if not period.get("open", True):
        return 0
    return int(period.get("remaining_cents", 0))


def ledger_entry_id(job):
    return f"{job['adjustment_id']}:{job['period']}"


def normalize_ledger_ids(job, entry_id):
    existing = []
    for current in job.setdefault("ledger_entry_ids", []):
        if current == entry_id and current not in existing:
            existing.append(current)
    if entry_id not in existing:
        existing.append(entry_id)
    job["ledger_entry_ids"] = existing


def post_adjustment(document, job):
    period = period_record(document, job["customer_id"], job["period"])
    if not period.get("open", True):
        return False

    amount = int(job["amount_cents"])
    entry_id = ledger_entry_id(job)
    entries = document.get("ledger", {}).get(job["customer_id"], {}).get("entries", {})
    existing = entries.get(entry_id, {})
    already_posted = int(existing.get("amount_cents", 0))
    if already_posted >= amount:
        normalize_ledger_ids(job, entry_id)
        return True

    needed = amount - already_posted
    remaining = int(period.get("remaining_cents", 0))
    if remaining < needed:
        return False

    ledger = ledger_record(document, job["customer_id"])
    entries = ledger.setdefault("entries", {})
    period["remaining_cents"] = remaining - needed
    entries[entry_id] = {
        "adjustment_id": job["adjustment_id"],
        "amount_cents": amount,
        "period": job["period"],
        "posted_by": existing.get("posted_by", "proration-worker"),
        "write_count": int(existing.get("write_count", 0)) + 1,
    }
    normalize_ledger_ids(job, entry_id)
    return True
PY

cat > /app/proration_rules.py <<'PY'
MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying"}
STALE_RETRY_METADATA = {"last_error", "retry_after", "retry_started_at", "next_retry_at"}


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
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    for key in STALE_RETRY_METADATA:
        metadata.pop(key, None)
    metadata["last_attempt_status"] = "posted"
    metadata["posted_at"] = "worker-run"
PY

cat > /app/proration_worker.py <<'PY'
import os
from pathlib import Path

import ledger_store
import proration_rules


def process_job(document, job):
    if not proration_rules.should_process(job):
        return job

    if ledger_store.post_adjustment(document, job):
        proration_rules.mark_posted(job)
        return job

    remaining = ledger_store.remaining_cents(document, job)
    reason = proration_rules.failure_reason(job, remaining)
    if proration_rules.should_retry(job, reason):
        proration_rules.mark_retrying(job, reason)
    else:
        proration_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = ledger_store.load_document(app_dir)
    for job in ledger_store.jobs(document):
        process_job(document, job)
    ledger_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("PRORATION_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()
PY

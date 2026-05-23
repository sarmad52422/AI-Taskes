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

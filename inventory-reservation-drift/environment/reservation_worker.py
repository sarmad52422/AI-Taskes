import os
from pathlib import Path

import inventory_store
import reservation_rules


def process_job(job, inventory):
    if not reservation_rules.should_process(job):
        return job

    if inventory_store.reserve_stock(inventory, job):
        reservation_rules.mark_reserved(job)
        return job

    available = inventory_store.available_quantity(inventory, job)
    reason = reservation_rules.failure_reason(job, available)
    if reservation_rules.should_retry(job, reason):
        reservation_rules.mark_retrying(job, reason)
    else:
        reservation_rules.mark_failed(job, reason)
    return job


def run(app_dir):
    jobs = inventory_store.read_jobs(app_dir)
    inventory = inventory_store.read_inventory(app_dir)
    for job in jobs:
        process_job(job, inventory)
    inventory_store.write_jobs(app_dir, jobs)
    inventory_store.write_inventory(app_dir, inventory)
    return jobs


def main():
    app_dir = Path(os.environ.get("RESERVATION_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()

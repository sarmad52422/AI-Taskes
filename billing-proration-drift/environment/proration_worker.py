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

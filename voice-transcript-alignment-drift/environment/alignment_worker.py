import os
from pathlib import Path

import alignment_rules
import alignment_store


def process_job(document, job):
    if not alignment_rules.should_process(job):
        return job

    ok, missing = alignment_store.align_segments(document, job)
    if ok:
        alignment_rules.mark_aligned(job)
        return job

    reason = alignment_rules.failure_reason(job, missing)
    if alignment_rules.should_retry(job, reason):
        alignment_rules.mark_retrying(job, reason)
    else:
        alignment_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = alignment_store.load_document(app_dir)
    for job in alignment_store.jobs(document):
        process_job(document, job)
    alignment_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("ALIGNMENT_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()

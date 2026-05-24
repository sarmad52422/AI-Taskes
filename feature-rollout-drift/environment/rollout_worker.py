import os
from pathlib import Path

import rollout_rules
import rollout_store


def process_job(document, job):
    if not rollout_rules.should_process(job):
        return job

    if rollout_store.assign_users(document, job):
        rollout_rules.mark_active(job)
        return job

    available = len(rollout_store.available_users(document, job))
    reason = rollout_rules.failure_reason(job, available)
    if rollout_rules.should_retry(job, reason):
        rollout_rules.mark_retrying(job, reason)
    else:
        rollout_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = rollout_store.load_document(app_dir)
    for job in rollout_store.jobs(document):
        process_job(document, job)
    rollout_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("ROLLOUT_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()

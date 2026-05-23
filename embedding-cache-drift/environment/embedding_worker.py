import os
from pathlib import Path

import embedding_rules
import embedding_store


def process_job(document, job):
    if not embedding_rules.should_process(job):
        return job

    ok, missing = embedding_store.write_embeddings(document, job)
    if ok:
        embedding_rules.mark_indexed(job)
        return job

    reason = embedding_rules.failure_reason(job, missing)
    if embedding_rules.should_retry(job, reason):
        embedding_rules.mark_retrying(job, reason)
    else:
        embedding_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = embedding_store.load_document(app_dir)
    for job in embedding_store.jobs(document):
        process_job(document, job)
    embedding_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("EMBEDDING_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()

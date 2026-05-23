#!/bin/bash
set -euo pipefail

cat > /app/embedding_store.py <<'PY'
import hashlib
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


def document_chunks(document, document_id):
    source = document.setdefault("documents", {}).setdefault(document_id, {})
    return source.setdefault("chunks", {})


def cache_for(document, document_id):
    return document.setdefault("embedding_cache", {}).setdefault(document_id, {})


def missing_chunks(document, job):
    chunks = document_chunks(document, job["document_id"])
    return [chunk_id for chunk_id in job.get("chunks", []) if chunk_id not in chunks]


def embedding_id(job, chunk_id):
    return f"{job['job_id']}:{job['model']}:{chunk_id}"


def vector_for(text, model):
    digest = hashlib.sha256(f"{model}:{text}".encode()).digest()
    return [round(byte / 255, 6) for byte in digest[:4]]


def normalize_embedding_ids(job, ids):
    existing = []
    for current in job.setdefault("embedding_ids", []):
        if current in ids and current not in existing:
            existing.append(current)
    for current in ids:
        if current not in existing:
            existing.append(current)
    job["embedding_ids"] = existing


def write_embeddings(document, job):
    missing = missing_chunks(document, job)
    if missing:
        return False, missing

    chunks = document_chunks(document, job["document_id"])
    cache = cache_for(document, job["document_id"])
    vectors = cache.setdefault("vectors", {})
    model = job["model"]
    expected_ids = [embedding_id(job, chunk_id) for chunk_id in job.get("chunks", [])]
    for chunk_id in job.get("chunks", []):
        current_id = embedding_id(job, chunk_id)
        existing = vectors.get(chunk_id)
        if existing and existing.get("embedding_id") == current_id and existing.get("model") == model:
            continue
        vectors[chunk_id] = {
            "embedding_id": current_id,
            "job_id": job["job_id"],
            "model": model,
            "text_sha": hashlib.sha1(chunks[chunk_id].encode()).hexdigest(),
            "vector": vector_for(chunks[chunk_id], model),
            "write_count": 1,
        }
    cache["model"] = model
    normalize_embedding_ids(job, expected_ids)
    return True, []
PY

cat > /app/embedding_rules.py <<'PY'
MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying"}
STALE_RETRY_METADATA = {"last_error", "retry_after", "retry_started_at", "next_retry_at"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, missing):
    requested = len(job.get("chunks", []))
    if len(missing) == requested:
        return "document_unavailable"
    return "partial_document"


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


def mark_indexed(job):
    job["status"] = "indexed"
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    for key in STALE_RETRY_METADATA:
        metadata.pop(key, None)
    metadata["last_attempt_status"] = "indexed"
    metadata["indexed_at"] = "worker-run"
PY

cat > /app/embedding_worker.py <<'PY'
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
PY

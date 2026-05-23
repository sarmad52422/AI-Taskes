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


def write_embeddings(document, job):
    missing = missing_chunks(document, job)
    if missing:
        return False, missing

    chunks = document_chunks(document, job["document_id"])
    cache = cache_for(document, job["document_id"])
    vectors = cache.setdefault("vectors", {})
    model = job["model"]
    for chunk_id in job.get("chunks", []):
        current_id = embedding_id(job, chunk_id)
        existing = vectors.get(chunk_id, {})
        vectors[chunk_id] = {
            "embedding_id": current_id,
            "job_id": job["job_id"],
            "model": model,
            "text_sha": hashlib.sha1(chunks[chunk_id].encode()).hexdigest(),
            "vector": vector_for(chunks[chunk_id], model),
            "write_count": int(existing.get("write_count", 0)) + 1,
        }
        job.setdefault("embedding_ids", []).append(current_id)
    cache["model"] = model
    return True, []

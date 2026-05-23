import copy
import json
import os
import subprocess
import sys
from pathlib import Path

APP_SOURCE = Path(os.environ.get("APP_SOURCE_DIR", "/app"))


def write_document(path, document):
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def read_document(path):
    return json.loads(path.read_text())


def run_worker(tmp_path, document):
    write_document(tmp_path / "jobs.json", copy.deepcopy(document))
    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(APP_SOURCE)!r}); "
        "import embedding_worker; "
        f"result = embedding_worker.run({str(tmp_path)!r}); "
        "print(json.dumps(result, sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(APP_SOURCE),
        text=True,
        capture_output=True,
        check=True,
    )
    returned = json.loads(completed.stdout)
    persisted = read_document(tmp_path / "jobs.json")
    return returned, persisted


def base_job(status="pending", retries=0, chunks=None, job_id="job-1"):
    return {
        "job_id": job_id,
        "status": status,
        "retries": retries,
        "document_id": "doc-a",
        "chunks": chunks or ["intro", "body"],
        "model": "embed-small-v1",
        "embedding_ids": [],
        "metadata": {"source": "indexer"},
    }


def documents_for(chunks):
    return {"doc-a": {"chunks": chunks}}


def cache_for(vectors=None):
    return {"doc-a": {"model": "embed-small-v1", "vectors": vectors or {}}}


def document_for(jobs, documents, cache=None):
    return {"jobs": jobs, "documents": documents, "embedding_cache": cache or {}}


def test_worker_returns_same_state_that_was_persisted(tmp_path):
    document = document_for(
        [base_job(chunks=["intro"])],
        documents_for({"intro": "hello vector search"}),
    )
    returned, persisted = run_worker(tmp_path, document)
    assert returned == persisted


def test_pending_job_writes_embeddings(tmp_path):
    document = document_for(
        [base_job()],
        documents_for({"intro": "hello", "body": "semantic search"}),
    )
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    vectors = persisted["embedding_cache"]["doc-a"]["vectors"]
    assert job["status"] == "indexed"
    assert job["embedding_ids"] == [
        "job-1:embed-small-v1:intro",
        "job-1:embed-small-v1:body",
    ]
    assert set(vectors) == {"intro", "body"}
    assert vectors["intro"]["model"] == "embed-small-v1"
    assert vectors["intro"]["write_count"] == 1


def test_partial_document_moves_to_retrying_without_cache_write(tmp_path):
    document = document_for(
        [base_job(chunks=["intro", "body"])],
        documents_for({"intro": "available chunk"}),
    )
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert job["failure_reason"] == "partial_document"
    assert persisted["embedding_cache"] == {}


def test_retrying_job_recovers_when_chunks_exist(tmp_path):
    job = base_job(status="retrying", retries=1, chunks=["intro"])
    job["failure_reason"] = "document_unavailable"
    job["metadata"].update({"last_error": "document_unavailable", "retry_after": "next-run"})
    document = document_for([job], documents_for({"intro": "now present"}))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "indexed"
    assert returned["jobs"][0]["embedding_ids"] == ["job-1:embed-small-v1:intro"]
    assert persisted["embedding_cache"]["doc-a"]["vectors"]["intro"]["write_count"] == 1


def test_exhausted_retry_moves_to_failed(tmp_path):
    job = base_job(status="retrying", retries=3, chunks=["intro"])
    job["failure_reason"] = "document_unavailable"
    document = document_for([job], documents_for({}))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "failed"
    assert returned["jobs"][0]["retries"] == 3
    assert returned["jobs"][0]["failure_reason"] == "document_unavailable"
    assert persisted["embedding_cache"] == {}


def test_completed_job_remains_unchanged(tmp_path):
    job = base_job(status="completed", chunks=["intro"], job_id="done-1")
    job["embedding_ids"] = ["done-1:embed-small-v1:intro"]
    job["metadata"] = {"source": "indexer", "completed_at": "2026-05-01T12:00:00Z"}
    cache = cache_for(
        {
            "intro": {
                "embedding_id": "done-1:embed-small-v1:intro",
                "job_id": "done-1",
                "model": "embed-small-v1",
                "text_sha": "kept",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "write_count": 1,
            }
        }
    )
    document = document_for([job], documents_for({"intro": "already done"}), cache)
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_failed_job_remains_unchanged(tmp_path):
    job = base_job(status="failed", retries=3, chunks=["intro"])
    job["failure_reason"] = "document_unavailable"
    job["metadata"].update({"last_error": "document_unavailable", "last_attempt_status": "failed"})
    document = document_for([job], documents_for({}))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_repeated_runs_do_not_rewrite_cache(tmp_path):
    document = document_for(
        [base_job(chunks=["intro"])],
        documents_for({"intro": "stable text"}),
    )
    returned, persisted = run_worker(tmp_path, document)
    first_vector = copy.deepcopy(persisted["embedding_cache"]["doc-a"]["vectors"]["intro"])
    returned, persisted = run_worker(tmp_path, returned)
    assert persisted["embedding_cache"]["doc-a"]["vectors"]["intro"] == first_vector
    assert persisted["embedding_cache"]["doc-a"]["vectors"]["intro"]["write_count"] == 1


def test_repeated_runs_do_not_duplicate_embedding_ids(tmp_path):
    document = document_for(
        [base_job(chunks=["intro"])],
        documents_for({"intro": "stable text"}),
    )
    returned, persisted = run_worker(tmp_path, document)
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["embedding_ids"] == ["job-1:embed-small-v1:intro"]
    assert list(persisted["embedding_cache"]["doc-a"]["vectors"]) == ["intro"]


def test_retry_success_clears_stale_metadata_and_failure_state(tmp_path):
    job = base_job(status="retrying", retries=2, chunks=["intro"])
    job["failure_reason"] = "partial_document"
    job["metadata"].update(
        {
            "last_error": "partial_document",
            "retry_after": "next-run",
            "retry_started_at": "2026-05-20T09:00:00Z",
            "customer_note": "keep priority",
        }
    )
    document = document_for([job], documents_for({"intro": "recovered"}))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert returned["jobs"][0]["status"] == "indexed"
    assert "failure_reason" not in returned["jobs"][0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert metadata["customer_note"] == "keep priority"
    assert persisted["embedding_cache"]["doc-a"]["vectors"]["intro"]["write_count"] == 1


def test_retry_recovery_tops_up_existing_partial_cache(tmp_path):
    job = base_job(status="retrying", retries=2, chunks=["intro", "body"])
    job["embedding_ids"] = [
        "job-1:embed-small-v1:intro",
        "job-1:embed-small-v1:intro",
    ]
    job["failure_reason"] = "partial_document"
    job["metadata"].update(
        {
            "last_error": "partial_document",
            "retry_after": "next-run",
            "customer_note": "batch A",
        }
    )
    existing_intro = {
        "embedding_id": "job-1:embed-small-v1:intro",
        "job_id": "job-1",
        "model": "embed-small-v1",
        "text_sha": "existing",
        "vector": [0.4, 0.3, 0.2, 0.1],
        "write_count": 1,
    }
    document = document_for(
        [job],
        documents_for({"intro": "already cached", "body": "new chunk"}),
        cache_for({"intro": existing_intro}),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    vectors = persisted["embedding_cache"]["doc-a"]["vectors"]
    assert recovered["status"] == "indexed"
    assert recovered["embedding_ids"] == [
        "job-1:embed-small-v1:intro",
        "job-1:embed-small-v1:body",
    ]
    assert "failure_reason" not in recovered
    assert vectors["intro"] == existing_intro
    assert vectors["body"]["write_count"] == 1
    assert recovered["metadata"]["customer_note"] == "batch A"


def test_original_metadata_is_preserved_on_success(tmp_path):
    job = base_job(chunks=["intro"])
    job["metadata"] = {"source": "indexer", "priority": "expedite"}
    document = document_for([job], documents_for({"intro": "metadata text"}))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert metadata["source"] == "indexer"
    assert metadata["priority"] == "expedite"
    assert persisted["embedding_cache"]["doc-a"]["vectors"]["intro"]["write_count"] == 1

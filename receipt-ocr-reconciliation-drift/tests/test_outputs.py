import copy
import importlib
import json
import sys
from pathlib import Path


APP = Path("/app")
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def field(key, raw, normalized=None, confidence=0.94):
    return {
        "key": key,
        "raw_value": raw,
        "normalized_value": normalized if normalized is not None else raw,
        "confidence": confidence,
    }


def page(checksum, *fields):
    return {"checksum": checksum, "fields": list(fields)}


def queued_job(**overrides):
    job = {
        "job_id": "job-77",
        "receipt_id": "receipt-77",
        "status": "queued",
        "attempts": 0,
        "merchant": "corner-market",
        "page_ids": ["front", "back"],
        "field_keys": ["merchant_name", "subtotal", "tax", "total"],
        "field_ids": [],
        "metadata": {"source": "mobile-upload", "operator_note": "batch c"},
    }
    job.update(overrides)
    return job


def ocr(front=None, back=None):
    pages = {}
    if front is not None:
        pages["front"] = front
    if back is not None:
        pages["back"] = back
    return {"receipt-77": pages}


def index(fields=None, count=0):
    return {"receipt-77": {"fields": copy.deepcopy(fields or {}), "reconciled_count": count}}


def document(job, pages, idx=None):
    return {
        "jobs": [copy.deepcopy(job)],
        "ocr_pages": copy.deepcopy(pages),
        "reconciliation_index": copy.deepcopy(idx or {}),
    }


def run_worker(tmp_path, payload):
    target = tmp_path / "receipts.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    module = importlib.import_module("reconcile_worker")
    module = importlib.reload(module)
    returned = module.run(str(target))
    persisted = json.loads(target.read_text(encoding="utf-8"))
    return returned, persisted


def good_pages(checksum="front-a"):
    return ocr(
        page(
            checksum,
            field("merchant_name", "Corner Market", "corner market"),
            field("subtotal", "$18.00", "18.00"),
            field("tax", "$1.44", "1.44"),
            field("total", "$19.44", "19.44"),
        )
    )


def indexed_record(key, job_id="job-77", checksum="front-a", raw="$1.00", normalized="1.00", page_id="front", writes=1):
    return {
        "field_id": f"{job_id}:{key}",
        "job_id": job_id,
        "page_id": page_id,
        "source_checksum": checksum,
        "raw_value": raw,
        "normalized_value": normalized,
        "confidence": 0.91,
        "write_count": writes,
    }


def test_return_value_is_the_saved_document(tmp_path):
    returned, persisted = run_worker(tmp_path, document(queued_job(), good_pages()))
    assert returned == persisted


def test_queued_job_reconciles_all_requested_fields(tmp_path):
    returned, _ = run_worker(tmp_path, document(queued_job(), good_pages()))
    job = returned["jobs"][0]
    bucket = returned["reconciliation_index"]["receipt-77"]
    assert job["status"] == "reconciled"
    assert job["field_ids"] == ["job-77:merchant_name", "job-77:subtotal", "job-77:tax", "job-77:total"]
    assert bucket["reconciled_count"] == 4
    assert bucket["fields"]["total"]["normalized_value"] == "19.44"


def test_missing_field_retries_without_partial_index_write(tmp_path):
    incomplete = ocr(page("front-a", field("merchant_name", "Corner Market"), field("subtotal", "$18.00", "18.00")))
    returned, persisted = run_worker(tmp_path, document(queued_job(), incomplete))
    job = returned["jobs"][0]
    assert job["status"] == "retrying"
    assert job["attempts"] == 1
    assert job["failure_reason"] == "missing_ocr_fields"
    assert job["metadata"]["partial_fields"] == ["tax", "total"]
    assert persisted["reconciliation_index"].get("receipt-77", {}).get("fields", {}) == {}


def test_low_confidence_field_is_not_reconciled(tmp_path):
    pages = ocr(
        page(
            "front-a",
            field("merchant_name", "Corner Market"),
            field("subtotal", "$18.00", "18.00"),
            field("tax", "$1.44", "1.44", confidence=0.51),
            field("total", "$19.44", "19.44"),
        )
    )
    returned, _ = run_worker(tmp_path, document(queued_job(status="retrying", attempts=1), pages))
    job = returned["jobs"][0]
    assert job["status"] == "retrying"
    assert job["attempts"] == 2
    assert job["failure_reason"] == "low_confidence_fields"
    assert job["metadata"]["partial_fields"] == ["tax"]


def test_retrying_job_recovers_when_ocr_is_complete(tmp_path):
    job = queued_job(status="retrying", attempts=2)
    job["failure_reason"] = "missing_ocr_fields"
    job["metadata"].update({"last_error": "missing_ocr_fields", "retry_after": "soon", "partial_fields": ["tax"]})
    returned, _ = run_worker(tmp_path, document(job, good_pages()))
    recovered = returned["jobs"][0]
    assert recovered["status"] == "reconciled"
    assert recovered["attempts"] == 2
    assert "failure_reason" not in recovered or recovered["failure_reason"] in (None, "")
    assert "last_error" not in recovered["metadata"]
    assert "retry_after" not in recovered["metadata"]
    assert "partial_fields" not in recovered["metadata"]
    assert recovered["metadata"]["operator_note"] == "batch c"


def test_exhausted_missing_ocr_fails(tmp_path):
    incomplete = ocr(page("front-a", field("merchant_name", "Corner Market")))
    returned, _ = run_worker(tmp_path, document(queued_job(status="retrying", attempts=3), incomplete))
    failed = returned["jobs"][0]
    assert failed["status"] == "failed"
    assert failed["attempts"] >= 3
    assert failed["failure_reason"] == "missing_ocr_fields"


def test_exported_job_is_left_exactly_as_it_was(tmp_path):
    job = queued_job(status="exported", attempts=0, field_ids=["old:total"])
    job["metadata"]["export_id"] = "exp-9"
    original = copy.deepcopy(job)
    returned, _ = run_worker(tmp_path, document(job, good_pages()))
    assert returned["jobs"][0] == original


def test_failed_job_is_left_exactly_as_it_was(tmp_path):
    job = queued_job(status="failed", attempts=3)
    job["failure_reason"] = "manual_reject"
    original = copy.deepcopy(job)
    returned, _ = run_worker(tmp_path, document(job, good_pages()))
    assert returned["jobs"][0] == original


def test_repeated_runs_do_not_add_counts_or_field_ids(tmp_path):
    payload = document(queued_job(), good_pages())
    first, _ = run_worker(tmp_path, payload)
    second, persisted = run_worker(tmp_path, first)
    job = second["jobs"][0]
    bucket = persisted["reconciliation_index"]["receipt-77"]
    assert job["field_ids"] == ["job-77:merchant_name", "job-77:subtotal", "job-77:tax", "job-77:total"]
    assert bucket["reconciled_count"] == 4
    assert bucket["fields"]["total"]["write_count"] == 1


def test_retry_recovery_uses_existing_partial_and_adds_missing_fields(tmp_path):
    job = queued_job(status="retrying", attempts=2)
    job["field_ids"] = ["job-77:merchant_name", "job-77:subtotal", "job-77:subtotal-old"]
    job["failure_reason"] = "missing_ocr_fields"
    seeded = {
        "merchant_name": indexed_record("merchant_name", raw="Corner Market", normalized="corner market"),
        "subtotal": indexed_record("subtotal", raw="$18.00", normalized="18.00"),
    }
    returned, persisted = run_worker(tmp_path, document(job, good_pages(), index(seeded, 2)))
    recovered = returned["jobs"][0]
    assert recovered["status"] == "reconciled"
    assert recovered["field_ids"] == ["job-77:merchant_name", "job-77:subtotal", "job-77:tax", "job-77:total"]
    assert persisted["reconciliation_index"]["receipt-77"]["reconciled_count"] == 4
    assert persisted["reconciliation_index"]["receipt-77"]["fields"]["subtotal"]["normalized_value"] == "18.00"


def test_stale_same_job_cache_is_refreshed_from_current_ocr(tmp_path):
    job = queued_job(status="retrying", attempts=2, field_keys=["total"], field_ids=["job-77:total"])
    job["failure_reason"] = "missing_ocr_fields"
    stale = {"total": indexed_record("total", checksum="front-old", raw="$18.99", normalized="18.99", writes=1)}
    pages = ocr(page("front-new", field("total", "$19.44", "19.44")))
    returned, persisted = run_worker(tmp_path, document(job, pages, index(stale, 1)))
    record = persisted["reconciliation_index"]["receipt-77"]["fields"]["total"]
    assert returned["jobs"][0]["status"] == "reconciled"
    assert record["source_checksum"] == "front-new"
    assert record["normalized_value"] == "19.44"
    assert record["write_count"] == 2
    assert persisted["reconciliation_index"]["receipt-77"]["reconciled_count"] == 1


def test_conflicting_field_owned_by_another_job_is_not_overwritten(tmp_path):
    job = queued_job(field_keys=["total"], page_ids=["front"])
    existing = {"total": indexed_record("total", job_id="other-job", raw="$17.00", normalized="17.00")}
    returned, persisted = run_worker(tmp_path, document(job, ocr(page("front-a", field("total", "$19.44", "19.44"))), index(existing, 1)))
    blocked = returned["jobs"][0]
    assert blocked["status"] == "retrying"
    assert blocked["attempts"] == 1
    assert blocked["failure_reason"] == "reconciliation_conflict"
    assert persisted["reconciliation_index"]["receipt-77"]["fields"] == existing


def test_conflict_retry_metadata_uses_conflict_reason(tmp_path):
    job = queued_job(status="retrying", attempts=1, field_keys=["total"], page_ids=["front"])
    job["metadata"].update({"last_error": "missing_ocr_fields", "retry_after": "after-attempt-1"})
    existing = {"total": indexed_record("total", job_id="other-job")}
    returned, _ = run_worker(tmp_path, document(job, ocr(page("front-a", field("total", "$19.44", "19.44"))), index(existing, 1)))
    blocked = returned["jobs"][0]
    assert blocked["status"] == "retrying"
    assert blocked["attempts"] == 2
    assert blocked["failure_reason"] == "reconciliation_conflict"
    assert blocked["metadata"]["last_error"] == "reconciliation_conflict"
    assert blocked["metadata"]["retry_after"].startswith("after-attempt-")


def test_exhausted_conflict_fails_without_touching_index(tmp_path):
    job = queued_job(status="retrying", attempts=3, field_keys=["total"], page_ids=["front"])
    existing = {"total": indexed_record("total", job_id="other-job")}
    returned, persisted = run_worker(tmp_path, document(job, ocr(page("front-a", field("total", "$19.44", "19.44"))), index(existing, 1)))
    failed = returned["jobs"][0]
    assert failed["status"] == "failed"
    assert failed["attempts"] >= 3
    assert failed["failure_reason"] == "reconciliation_conflict"
    assert persisted["reconciliation_index"]["receipt-77"]["fields"] == existing


def test_requested_field_order_is_kept_after_recovery(tmp_path):
    job = queued_job(
        status="retrying",
        attempts=2,
        field_keys=["total", "merchant_name", "tax"],
        field_ids=["job-77:tax", "job-77:merchant_name"],
    )
    returned, _ = run_worker(tmp_path, document(job, good_pages()))
    assert returned["jobs"][0]["field_ids"] == ["job-77:total", "job-77:merchant_name", "job-77:tax"]


def test_needs_review_job_is_not_reopened(tmp_path):
    job = queued_job(status="needs_review", attempts=2)
    job["failure_reason"] = "operator_queue"
    original = copy.deepcopy(job)
    returned, _ = run_worker(tmp_path, document(job, good_pages()))
    assert returned["jobs"][0] == original

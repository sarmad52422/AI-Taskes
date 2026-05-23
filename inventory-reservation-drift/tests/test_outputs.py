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
        "import reservation_worker; "
        f"result = reservation_worker.run({str(tmp_path)!r}); "
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


def base_job(status="pending", retries=0, quantity=2, reservation_id="res-1"):
    return {
        "reservation_id": reservation_id,
        "status": status,
        "retries": retries,
        "item_id": "sku-a",
        "quantity": quantity,
        "warehouse": "east",
        "reservation_ids": [],
        "metadata": {"source": "orders"},
    }


def inventory_for(available, reserved=None):
    return {
        "east": {
            "sku-a": {
                "available": available,
                "reserved": reserved or {},
            }
        }
    }


def document_for(jobs, inventory):
    return {"jobs": jobs, "inventory": inventory}


def test_worker_returns_same_state_that_was_persisted(tmp_path):
    document = document_for([base_job(quantity=1)], inventory_for(5))
    returned, persisted = run_worker(tmp_path, document)
    assert returned == persisted


def test_pending_reservation_reserves_stock(tmp_path):
    document = document_for([base_job()], inventory_for(5))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    item = persisted["inventory"]["east"]["sku-a"]
    assert job["status"] == "reserved"
    assert job["reservation_ids"] == ["res-1"]
    assert item["available"] == 3
    assert item["reserved"] == {"res-1": 2}


def test_insufficient_partial_stock_moves_to_retrying(tmp_path):
    document = document_for([base_job(quantity=5)], inventory_for(3))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    item = persisted["inventory"]["east"]["sku-a"]
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert job["failure_reason"] == "partial_stock"
    assert item["available"] == 3
    assert item["reserved"] == {}


def test_retrying_reservation_recovers_when_stock_exists(tmp_path):
    job = base_job(status="retrying", retries=1, quantity=3)
    job["failure_reason"] = "out_of_stock"
    job["metadata"].update({"last_error": "out_of_stock", "retry_after": "next-run"})
    document = document_for([job], inventory_for(4))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "reserved"
    assert persisted["inventory"]["east"]["sku-a"]["available"] == 1
    assert persisted["inventory"]["east"]["sku-a"]["reserved"] == {"res-1": 3}


def test_exhausted_retry_moves_to_failed(tmp_path):
    job = base_job(status="retrying", retries=3, quantity=1)
    job["failure_reason"] = "out_of_stock"
    document = document_for([job], inventory_for(0))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "failed"
    assert returned["jobs"][0]["retries"] == 3
    assert returned["jobs"][0]["failure_reason"] == "out_of_stock"
    assert persisted["inventory"]["east"]["sku-a"]["available"] == 0


def test_completed_job_remains_unchanged(tmp_path):
    job = base_job(status="completed", reservation_id="done-1")
    job["reservation_ids"] = ["done-1"]
    job["metadata"] = {"source": "orders", "completed_at": "2026-05-01T12:00:00Z"}
    document = document_for([job], inventory_for(7, {"done-1": 2}))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_failed_job_remains_unchanged(tmp_path):
    job = base_job(status="failed", retries=3)
    job["failure_reason"] = "out_of_stock"
    job["metadata"].update(
        {"last_error": "out_of_stock", "last_attempt_status": "failed"}
    )
    document = document_for([job], inventory_for(0))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_repeated_runs_do_not_double_deduct_inventory(tmp_path):
    document = document_for([base_job(quantity=2)], inventory_for(9))
    returned, persisted = run_worker(tmp_path, document)
    first_available = persisted["inventory"]["east"]["sku-a"]["available"]
    returned, persisted = run_worker(tmp_path, returned)
    assert persisted["inventory"]["east"]["sku-a"]["available"] == first_available
    assert persisted["inventory"]["east"]["sku-a"]["available"] == 7
    assert persisted["inventory"]["east"]["sku-a"]["reserved"] == {"res-1": 2}


def test_repeated_runs_do_not_duplicate_reservation_ids(tmp_path):
    document = document_for([base_job(quantity=1)], inventory_for(5))
    returned, persisted = run_worker(tmp_path, document)
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["reservation_ids"] == ["res-1"]
    assert persisted["inventory"]["east"]["sku-a"]["reserved"] == {"res-1": 1}


def test_retry_success_clears_stale_metadata_and_failure_state(tmp_path):
    job = base_job(status="retrying", retries=2, quantity=2)
    job["failure_reason"] = "partial_stock"
    job["metadata"].update(
        {
            "last_error": "partial_stock",
            "retry_after": "next-run",
            "retry_started_at": "2026-05-20T09:00:00Z",
            "customer_note": "ship together",
        }
    )
    document = document_for([job], inventory_for(6))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert returned["jobs"][0]["status"] == "reserved"
    assert "failure_reason" not in returned["jobs"][0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert metadata["customer_note"] == "ship together"
    assert persisted["inventory"]["east"]["sku-a"]["available"] == 4


def test_retry_recovery_tops_up_existing_partial_hold(tmp_path):
    job = base_job(status="retrying", retries=2, quantity=5)
    job["reservation_ids"] = ["res-1", "res-1"]
    job["failure_reason"] = "partial_stock"
    job["metadata"].update(
        {
            "last_error": "partial_stock",
            "retry_after": "next-run",
            "customer_note": "hold together",
        }
    )
    document = document_for([job], inventory_for(3, {"res-1": 2}))
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    item = persisted["inventory"]["east"]["sku-a"]
    assert recovered["status"] == "reserved"
    assert recovered["reservation_ids"] == ["res-1"]
    assert "failure_reason" not in recovered
    assert "last_error" not in recovered["metadata"]
    assert recovered["metadata"]["customer_note"] == "hold together"
    assert item["available"] == 0
    assert item["reserved"] == {"res-1": 5}


def test_original_metadata_is_preserved_on_success(tmp_path):
    job = base_job(quantity=2)
    job["metadata"] = {"source": "orders", "priority": "expedite"}
    document = document_for([job], inventory_for(8))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert metadata["source"] == "orders"
    assert metadata["priority"] == "expedite"
    assert persisted["inventory"]["east"]["sku-a"]["available"] == 6

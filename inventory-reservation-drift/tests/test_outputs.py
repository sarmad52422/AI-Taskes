import copy
import json
import os
import subprocess
import sys
from pathlib import Path


APP_SOURCE = Path(os.environ.get("APP_SOURCE_DIR", "/app"))


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path):
    return json.loads(path.read_text())


def run_worker(tmp_path, jobs, inventory):
    write_json(tmp_path / "jobs.json", copy.deepcopy(jobs))
    write_json(tmp_path / "inventory.json", copy.deepcopy(inventory))
    env = os.environ.copy()
    env["RESERVATION_APP_DIR"] = str(tmp_path)
    subprocess.run(
        [sys.executable, str(APP_SOURCE / "reservation_worker.py")],
        cwd=str(APP_SOURCE),
        env=env,
        check=True,
    )
    return read_json(tmp_path / "jobs.json"), read_json(tmp_path / "inventory.json")


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


def test_pending_reservation_holds_stock(tmp_path):
    jobs, inventory = run_worker(tmp_path, [base_job()], inventory_for(5))
    assert jobs[0]["status"] == "reserved"
    assert jobs[0]["reservation_ids"] == ["res-1"]
    assert inventory["east"]["sku-a"]["available"] == 3
    assert inventory["east"]["sku-a"]["reserved"] == {"res-1": 2}


def test_retrying_reservation_is_reprocessed(tmp_path):
    job = base_job(status="retrying", retries=1, quantity=3)
    job["failure_reason"] = "out_of_stock"
    job["metadata"].update({"last_error": "out_of_stock", "retry_after": "next-run"})
    jobs, inventory = run_worker(tmp_path, [job], inventory_for(4))
    assert jobs[0]["status"] == "reserved"
    assert inventory["east"]["sku-a"]["available"] == 1
    assert inventory["east"]["sku-a"]["reserved"] == {"res-1": 3}


def test_completed_reservation_is_not_mutated(tmp_path):
    job = base_job(status="completed", reservation_id="done-1")
    job["reservation_ids"] = ["done-1"]
    job["metadata"] = {"source": "orders", "completed_at": "2026-05-01T12:00:00Z"}
    inventory = inventory_for(7, {"done-1": 2})
    original_job = copy.deepcopy(job)
    original_inventory = copy.deepcopy(inventory)
    jobs, updated_inventory = run_worker(tmp_path, [job], inventory)
    assert jobs[0] == original_job
    assert updated_inventory == original_inventory


def test_failed_reservation_is_not_mutated(tmp_path):
    job = base_job(status="failed", retries=3)
    job["failure_reason"] = "out_of_stock"
    job["metadata"].update({"last_error": "out_of_stock", "last_attempt_status": "failed"})
    inventory = inventory_for(0)
    original_job = copy.deepcopy(job)
    jobs, updated_inventory = run_worker(tmp_path, [job], inventory)
    assert jobs[0] == original_job
    assert updated_inventory == inventory


def test_repeated_runs_do_not_duplicate_reservation_ids(tmp_path):
    job = base_job(quantity=1)
    inventory = inventory_for(5)
    jobs, inventory = run_worker(tmp_path, [job], inventory)
    jobs, inventory = run_worker(tmp_path, jobs, inventory)
    assert jobs[0]["reservation_ids"] == ["res-1"]
    assert inventory["east"]["sku-a"]["reserved"] == {"res-1": 1}


def test_repeated_runs_do_not_consume_additional_inventory(tmp_path):
    job = base_job(quantity=2)
    inventory = inventory_for(9)
    jobs, inventory = run_worker(tmp_path, [job], inventory)
    available_after_first = inventory["east"]["sku-a"]["available"]
    jobs, inventory = run_worker(tmp_path, jobs, inventory)
    assert inventory["east"]["sku-a"]["available"] == available_after_first
    assert inventory["east"]["sku-a"]["available"] == 7


def test_retry_success_cleans_stale_failure_state(tmp_path):
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
    jobs, inventory = run_worker(tmp_path, [job], inventory_for(6))
    metadata = jobs[0]["metadata"]
    assert jobs[0]["status"] == "reserved"
    assert "failure_reason" not in jobs[0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert "retry_started_at" not in metadata
    assert metadata["customer_note"] == "ship together"
    assert inventory["east"]["sku-a"]["available"] == 4


def test_partial_shortage_moves_to_retrying(tmp_path):
    job = base_job(quantity=5)
    jobs, inventory = run_worker(tmp_path, [job], inventory_for(3))
    assert jobs[0]["status"] == "retrying"
    assert jobs[0]["retries"] == 1
    assert jobs[0]["failure_reason"] == "partial_stock"
    assert inventory["east"]["sku-a"]["available"] == 3
    assert inventory["east"]["sku-a"]["reserved"] == {}


def test_retry_limit_exhaustion_fails_reservation(tmp_path):
    job = base_job(status="retrying", retries=3, quantity=1)
    job["failure_reason"] = "out_of_stock"
    jobs, inventory = run_worker(tmp_path, [job], inventory_for(0))
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["retries"] == 3
    assert jobs[0]["failure_reason"] == "out_of_stock"
    assert inventory["east"]["sku-a"]["available"] == 0


def test_retry_below_limit_remains_retrying(tmp_path):
    job = base_job(status="retrying", retries=1, quantity=1)
    jobs, inventory = run_worker(tmp_path, [job], inventory_for(0))
    assert jobs[0]["status"] == "retrying"
    assert jobs[0]["retries"] == 2
    assert jobs[0]["failure_reason"] == "out_of_stock"
    assert inventory["east"]["sku-a"]["reserved"] == {}


def test_mixed_batch_only_processes_active_jobs(tmp_path):
    pending = base_job(quantity=2, reservation_id="new-1")
    completed = base_job(status="completed", reservation_id="done-1")
    completed["reservation_ids"] = ["done-1"]
    failed = base_job(status="failed", retries=3, reservation_id="bad-1")
    failed["failure_reason"] = "out_of_stock"
    failed["metadata"]["last_error"] = "out_of_stock"
    inventory = inventory_for(6, {"done-1": 1})
    jobs, inventory = run_worker(tmp_path, [pending, completed, failed], inventory)
    assert jobs[0]["status"] == "reserved"
    assert jobs[1] == completed
    assert jobs[2] == failed
    assert inventory["east"]["sku-a"]["available"] == 4
    assert inventory["east"]["sku-a"]["reserved"] == {"done-1": 1, "new-1": 2}


def test_existing_reserved_record_is_stable(tmp_path):
    job = base_job(status="reserved", quantity=2)
    job["reservation_ids"] = ["res-1"]
    inventory = inventory_for(4, {"res-1": 2})
    jobs, updated_inventory = run_worker(tmp_path, [job], inventory)
    assert jobs[0] == job
    assert updated_inventory == inventory

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
    write_document(tmp_path / "events.json", copy.deepcopy(document))
    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(APP_SOURCE)!r}); "
        "import proration_worker; "
        f"result = proration_worker.run({str(tmp_path)!r}); "
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
    persisted = read_document(tmp_path / "events.json")
    return returned, persisted


def base_job(status="pending", retries=0, amount_cents=1000, adjustment_id="adj-1"):
    return {
        "adjustment_id": adjustment_id,
        "status": status,
        "retries": retries,
        "customer_id": "acct-a",
        "period": "2026-05",
        "amount_cents": amount_cents,
        "ledger_entry_ids": [],
        "metadata": {"source": "billing"},
    }


def accounts_for(remaining, open_period=True):
    return {
        "acct-a": {
            "periods": {
                "2026-05": {
                    "open": open_period,
                    "remaining_cents": remaining,
                }
            }
        }
    }


def ledger_for(entries=None):
    return {"acct-a": {"entries": entries or {}}}


def document_for(jobs, accounts, ledger=None):
    return {"jobs": jobs, "accounts": accounts, "ledger": ledger or {}}


def test_worker_returns_same_state_that_was_persisted(tmp_path):
    document = document_for([base_job(amount_cents=300)], accounts_for(1000))
    returned, persisted = run_worker(tmp_path, document)
    assert returned == persisted


def test_pending_adjustment_posts_ledger_entry(tmp_path):
    document = document_for([base_job()], accounts_for(2500))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    period = persisted["accounts"]["acct-a"]["periods"]["2026-05"]
    entries = persisted["ledger"]["acct-a"]["entries"]
    assert job["status"] == "posted"
    assert job["ledger_entry_ids"] == ["adj-1:2026-05"]
    assert period["remaining_cents"] == 1500
    assert entries["adj-1:2026-05"]["amount_cents"] == 1000
    assert entries["adj-1:2026-05"]["write_count"] == 1


def test_partial_period_balance_moves_to_retrying_without_post(tmp_path):
    document = document_for([base_job(amount_cents=1200)], accounts_for(700))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    period = persisted["accounts"]["acct-a"]["periods"]["2026-05"]
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert job["failure_reason"] == "partial_period_balance"
    assert period["remaining_cents"] == 700
    assert persisted["ledger"] == {}


def test_retrying_adjustment_recovers_when_balance_exists(tmp_path):
    job = base_job(status="retrying", retries=1, amount_cents=800)
    job["failure_reason"] = "period_unavailable"
    job["metadata"].update({"last_error": "period_unavailable", "retry_after": "next-run"})
    document = document_for([job], accounts_for(1000))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "posted"
    assert persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"] == 200
    assert persisted["ledger"]["acct-a"]["entries"]["adj-1:2026-05"]["amount_cents"] == 800


def test_exhausted_retry_moves_to_failed(tmp_path):
    job = base_job(status="retrying", retries=3, amount_cents=500)
    job["failure_reason"] = "period_unavailable"
    document = document_for([job], accounts_for(0))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "failed"
    assert returned["jobs"][0]["retries"] == 3
    assert returned["jobs"][0]["failure_reason"] == "period_unavailable"
    assert persisted["ledger"] == {}


def test_completed_adjustment_remains_unchanged(tmp_path):
    job = base_job(status="completed", adjustment_id="done-1")
    job["ledger_entry_ids"] = ["done-1:2026-05"]
    job["metadata"] = {"source": "billing", "completed_at": "2026-05-01T12:00:00Z"}
    entry = {
        "adjustment_id": "done-1",
        "amount_cents": 1000,
        "period": "2026-05",
        "posted_by": "billing",
        "write_count": 1,
    }
    document = document_for([job], accounts_for(5000), ledger_for({"done-1:2026-05": entry}))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_failed_adjustment_remains_unchanged(tmp_path):
    job = base_job(status="failed", retries=3)
    job["failure_reason"] = "period_unavailable"
    job["metadata"].update({"last_error": "period_unavailable", "last_attempt_status": "failed"})
    document = document_for([job], accounts_for(0))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_repeated_runs_do_not_double_post_amount(tmp_path):
    document = document_for([base_job(amount_cents=600)], accounts_for(2000))
    returned, persisted = run_worker(tmp_path, document)
    first_remaining = persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"]
    returned, persisted = run_worker(tmp_path, returned)
    entry = persisted["ledger"]["acct-a"]["entries"]["adj-1:2026-05"]
    assert persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"] == first_remaining
    assert entry["amount_cents"] == 600
    assert entry["write_count"] == 1


def test_repeated_runs_do_not_duplicate_ledger_entry_ids(tmp_path):
    document = document_for([base_job(amount_cents=500)], accounts_for(1200))
    returned, persisted = run_worker(tmp_path, document)
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["ledger_entry_ids"] == ["adj-1:2026-05"]
    assert list(persisted["ledger"]["acct-a"]["entries"]) == ["adj-1:2026-05"]


def test_retry_success_clears_stale_metadata_and_failure_state(tmp_path):
    job = base_job(status="retrying", retries=2, amount_cents=700)
    job["failure_reason"] = "partial_period_balance"
    job["metadata"].update(
        {
            "last_error": "partial_period_balance",
            "retry_after": "next-run",
            "retry_started_at": "2026-05-20T09:00:00Z",
            "invoice_note": "keep this note",
        }
    )
    document = document_for([job], accounts_for(900))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert returned["jobs"][0]["status"] == "posted"
    assert "failure_reason" not in returned["jobs"][0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert metadata["invoice_note"] == "keep this note"
    assert persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"] == 200


def test_retry_recovery_tops_up_existing_partial_ledger_entry(tmp_path):
    job = base_job(status="retrying", retries=2, amount_cents=1000)
    job["ledger_entry_ids"] = ["adj-1:2026-05", "adj-1:2026-05"]
    job["failure_reason"] = "partial_period_balance"
    job["metadata"].update(
        {
            "last_error": "partial_period_balance",
            "retry_after": "next-run",
            "invoice_note": "annual plan change",
        }
    )
    existing_entry = {
        "adjustment_id": "adj-1",
        "amount_cents": 400,
        "period": "2026-05",
        "posted_by": "billing",
        "write_count": 1,
    }
    document = document_for(
        [job],
        accounts_for(600),
        ledger_for({"adj-1:2026-05": existing_entry}),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    entry = persisted["ledger"]["acct-a"]["entries"]["adj-1:2026-05"]
    assert recovered["status"] == "posted"
    assert recovered["ledger_entry_ids"] == ["adj-1:2026-05"]
    assert "failure_reason" not in recovered
    assert "last_error" not in recovered["metadata"]
    assert recovered["metadata"]["invoice_note"] == "annual plan change"
    assert persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"] == 0
    assert entry["amount_cents"] == 1000
    assert entry["write_count"] == 2


def test_original_metadata_is_preserved_on_success(tmp_path):
    job = base_job(amount_cents=300)
    job["metadata"] = {"source": "billing", "priority": "manual-review"}
    document = document_for([job], accounts_for(1000))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert metadata["source"] == "billing"
    assert metadata["priority"] == "manual-review"
    assert persisted["accounts"]["acct-a"]["periods"]["2026-05"]["remaining_cents"] == 700

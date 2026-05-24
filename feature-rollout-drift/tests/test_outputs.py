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
    write_document(tmp_path / "rollouts.json", copy.deepcopy(document))
    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(APP_SOURCE)!r}); "
        "import rollout_worker; "
        f"result = rollout_worker.run({str(tmp_path)!r}); "
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
    persisted = read_document(tmp_path / "rollouts.json")
    return returned, persisted


def base_job(status="pending", retries=0, target_count=2, rollout_id="rollout-1"):
    return {
        "rollout_id": rollout_id,
        "status": status,
        "retries": retries,
        "feature_key": "feature-a",
        "segment": "beta",
        "target_count": target_count,
        "assignment_ids": [],
        "metadata": {"source": "launch"},
    }


def segments_for(users):
    return {"beta": {"eligible_users": users}}


def assignments_for(users=None, assigned_count=0):
    return {"feature-a": {"beta": {"assigned_count": assigned_count, "users": users or {}}}}


def document_for(jobs, segments, assignments=None):
    return {"jobs": jobs, "segments": segments, "assignments": assignments or {}}


def test_worker_returns_same_state_that_was_persisted(tmp_path):
    document = document_for([base_job(target_count=1)], segments_for(["u1", "u2"]))
    returned, persisted = run_worker(tmp_path, document)
    assert returned == persisted


def test_pending_rollout_assigns_users(tmp_path):
    document = document_for([base_job()], segments_for(["u1", "u2", "u3"]))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    record = persisted["assignments"]["feature-a"]["beta"]
    assert job["status"] == "active"
    assert job["assignment_ids"] == ["rollout-1:u1", "rollout-1:u2"]
    assert record["assigned_count"] == 2
    assert set(record["users"]) == {"u1", "u2"}
    assert record["users"]["u1"]["write_count"] == 1


def test_partial_audience_moves_to_retrying_without_assignments(tmp_path):
    document = document_for([base_job(target_count=3)], segments_for(["u1", "u2"]))
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert job["failure_reason"] == "partial_audience"
    assert persisted["assignments"] == {}


def test_retrying_rollout_recovers_when_audience_exists(tmp_path):
    job = base_job(status="retrying", retries=1, target_count=1)
    job["failure_reason"] = "audience_exhausted"
    job["metadata"].update({"last_error": "audience_exhausted", "retry_after": "next-run"})
    document = document_for([job], segments_for(["u1"]))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "active"
    assert returned["jobs"][0]["assignment_ids"] == ["rollout-1:u1"]
    assert persisted["assignments"]["feature-a"]["beta"]["assigned_count"] == 1


def test_exhausted_retry_moves_to_failed(tmp_path):
    job = base_job(status="retrying", retries=3, target_count=1)
    job["failure_reason"] = "audience_exhausted"
    document = document_for([job], segments_for([]))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "failed"
    assert returned["jobs"][0]["retries"] == 3
    assert returned["jobs"][0]["failure_reason"] == "audience_exhausted"
    assert persisted["assignments"] == {}


def test_completed_rollout_remains_unchanged(tmp_path):
    job = base_job(status="completed", target_count=1, rollout_id="done-1")
    job["assignment_ids"] = ["done-1:u1"]
    job["metadata"] = {"source": "launch", "completed_at": "2026-05-01T12:00:00Z"}
    users = {
        "u1": {
            "assignment_id": "done-1:u1",
            "rollout_id": "done-1",
            "status": "active",
            "write_count": 1,
        }
    }
    document = document_for([job], segments_for(["u1"]), assignments_for(users, 1))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_failed_rollout_remains_unchanged(tmp_path):
    job = base_job(status="failed", retries=3, target_count=1)
    job["failure_reason"] = "audience_exhausted"
    job["metadata"].update({"last_error": "audience_exhausted", "last_attempt_status": "failed"})
    document = document_for([job], segments_for([]))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_repeated_runs_do_not_change_assigned_count(tmp_path):
    document = document_for([base_job(target_count=2)], segments_for(["u1", "u2", "u3"]))
    returned, persisted = run_worker(tmp_path, document)
    first_record = copy.deepcopy(persisted["assignments"]["feature-a"]["beta"])
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["status"] == "active"
    assert persisted["assignments"]["feature-a"]["beta"] == first_record


def test_repeated_runs_do_not_duplicate_assignment_ids(tmp_path):
    document = document_for([base_job(target_count=1)], segments_for(["u1", "u2"]))
    returned, persisted = run_worker(tmp_path, document)
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["assignment_ids"] == ["rollout-1:u1"]
    assert list(persisted["assignments"]["feature-a"]["beta"]["users"]) == ["u1"]


def test_retry_success_clears_stale_metadata_and_failure_state(tmp_path):
    job = base_job(status="retrying", retries=2, target_count=1)
    job["failure_reason"] = "partial_audience"
    job["metadata"].update(
        {
            "last_error": "partial_audience",
            "retry_after": "next-run",
            "retry_started_at": "2026-05-20T09:00:00Z",
            "owner_note": "keep staged rollout",
        }
    )
    document = document_for([job], segments_for(["u1"]))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert returned["jobs"][0]["status"] == "active"
    assert "failure_reason" not in returned["jobs"][0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert metadata["owner_note"] == "keep staged rollout"
    assert persisted["assignments"]["feature-a"]["beta"]["assigned_count"] == 1


def test_retry_recovery_tops_up_existing_partial_assignments(tmp_path):
    job = base_job(status="retrying", retries=2, target_count=3)
    job["assignment_ids"] = ["rollout-1:u1", "rollout-1:u1"]
    job["failure_reason"] = "partial_audience"
    job["metadata"].update(
        {
            "last_error": "partial_audience",
            "retry_after": "next-run",
            "owner_note": "beta ramp",
        }
    )
    existing = {
        "u1": {
            "assignment_id": "rollout-1:u1",
            "rollout_id": "rollout-1",
            "status": "active",
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        segments_for(["u1", "u2", "u3", "u4"]),
        assignments_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    record = persisted["assignments"]["feature-a"]["beta"]
    assert recovered["status"] == "active"
    assert recovered["assignment_ids"] == ["rollout-1:u1", "rollout-1:u2", "rollout-1:u3"]
    assert "failure_reason" not in recovered
    assert "last_error" not in recovered["metadata"]
    assert recovered["metadata"]["owner_note"] == "beta ramp"
    assert record["assigned_count"] == 3
    assert record["users"]["u1"] == existing["u1"]
    assert record["users"]["u2"]["write_count"] == 1


def test_original_metadata_is_preserved_on_success(tmp_path):
    job = base_job(target_count=1)
    job["metadata"] = {"source": "launch", "priority": "pm-approved"}
    document = document_for([job], segments_for(["u1"]))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert metadata["source"] == "launch"
    assert metadata["priority"] == "pm-approved"
    assert persisted["assignments"]["feature-a"]["beta"]["assigned_count"] == 1

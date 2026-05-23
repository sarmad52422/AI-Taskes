import copy
import json
from pathlib import Path

from pipeline import process_jobs
from storage import load_jobs

DATA_PATH = Path("/app/jobs.json")
BASE_JOBS = [
    {
        "id": "job-readable-malformed",
        "status": "pending",
        "retries": 0,
        "events": [
            {"kind": "session_note", "text": "Player waited for the cue before moving."},
            {"kind": "coach_note", "text": "Timing improved late in the round."},
        ],
    },
    {
        "id": "job-json-alias",
        "status": "pending",
        "retries": 0,
        "events": [
            {"kind": "session_note", "text": "Player paused after mistakes and recovered focus."}
        ],
    },
    {
        "id": "job-low-confidence",
        "status": "pending",
        "retries": 0,
        "events": [
            {"kind": "session_note", "text": "Player guessed early and missed the timing window."}
        ],
    },
    {"id": "job-empty-events", "status": "pending", "retries": 0, "events": []},
    {"id": "job-retrying-empty", "status": "retrying", "retries": 1, "events": []},
    {
        "id": "job-already-complete",
        "status": "completed",
        "retries": 0,
        "summary": "Player slowed down and found the right rhythm.",
        "skill": "timing",
        "confidence": 0.91,
        "reviewed_by": "coach-17",
        "events": [
            {"kind": "session_note", "text": "Historical session that has already been summarized."}
        ],
    },
]


def setup_function():
    DATA_PATH.write_text(json.dumps(BASE_JOBS, indent=2) + "\n", encoding="utf-8")


def find_job(jobs, job_id):
    return next(job for job in jobs if job["id"] == job_id)


def test_worker_persists_the_returned_state():
    jobs = process_jobs()
    assert jobs == load_jobs()


def test_readable_non_json_response_is_completed_when_confident():
    job = find_job(process_jobs(), "job-readable-malformed")
    assert job["status"] == "completed"
    assert job["summary"] == "Player improved timing"
    assert job["skill"] == "patience"
    assert job["confidence"] == 0.86
    assert job["retries"] == 0


def test_json_alias_fields_are_normalized():
    job = find_job(process_jobs(), "job-json-alias")
    assert job["status"] == "completed"
    assert job["summary"] == "Player stayed calm under pressure"
    assert job["skill"] == "focus"
    assert job["confidence"] == 0.82


def test_low_confidence_response_does_not_complete():
    job = find_job(process_jobs(), "job-low-confidence")
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert "summary" not in job
    assert "skill" not in job
    assert "confidence" not in job


def test_retrying_unusable_job_moves_to_failed():
    job = find_job(process_jobs(), "job-retrying-empty")
    assert job["status"] == "failed"
    assert job["retries"] == 2


def test_new_unusable_job_waits_for_another_attempt():
    job = find_job(process_jobs(), "job-empty-events")
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert "summary" not in job


def test_completed_job_is_not_rewritten():
    expected = copy.deepcopy(find_job(BASE_JOBS, "job-already-complete"))
    job = find_job(process_jobs(), "job-already-complete")
    assert job == expected


def test_original_event_history_is_preserved():
    expected = {job["id"]: copy.deepcopy(job["events"]) for job in BASE_JOBS}
    jobs = process_jobs()
    assert {job["id"]: job["events"] for job in jobs} == expected


def test_second_run_does_not_mutate_completed_results():
    first_jobs = process_jobs()
    readable = copy.deepcopy(find_job(first_jobs, "job-readable-malformed"))
    alias = copy.deepcopy(find_job(first_jobs, "job-json-alias"))
    second_jobs = process_jobs()
    assert find_job(second_jobs, "job-readable-malformed") == readable
    assert find_job(second_jobs, "job-json-alias") == alias


def test_completed_jobs_have_reviewable_fields():
    jobs = process_jobs()
    completed = [job for job in jobs if job["status"] == "completed"]
    assert len(completed) == 3
    assert all(isinstance(job.get("summary"), str) and job["summary"].strip() for job in completed)
    assert all(isinstance(job.get("skill"), str) and job["skill"].strip() for job in completed)
    assert all(isinstance(job.get("confidence"), float) and job["confidence"] >= 0.7 for job in completed)

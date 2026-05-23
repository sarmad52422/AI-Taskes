import copy
import json
import os
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(os.environ.get("TASK_ROOT_FOR_TESTS", "/app"))
sys.path.insert(0, str(APP_ROOT))

from pipeline import process_jobs
from storage import load_jobs


DATA_PATH = APP_ROOT / "jobs.json"
BASE_JOBS = [
    {
        "id": "job-readable-malformed",
        "status": "pending",
        "retries": 0,
        "events": [
            {
                "kind": "session_note",
                "text": "Player practiced waiting for the cue before moving.",
            },
            {"kind": "coach_note", "text": "Timing improved late in the round."},
        ],
    },
    {
        "id": "job-already-complete",
        "status": "completed",
        "retries": 0,
        "summary": "Player slowed down and found the right rhythm.",
        "skill": "timing",
        "events": [
            {
                "kind": "session_note",
                "text": "Historical session that has already been summarized.",
            }
        ],
    },
    {"id": "job-empty-events", "status": "pending", "retries": 0, "events": []},
]


def find_job(jobs, job_id):
    return next(job for job in jobs if job["id"] == job_id)


@pytest.fixture(autouse=True)
def reset_jobs_file():
    DATA_PATH.write_text(json.dumps(BASE_JOBS, indent=2) + "\n", encoding="utf-8")
    yield


def test_malformed_response_does_not_crash_worker():
    jobs = process_jobs()

    assert jobs == load_jobs()


def test_readable_malformed_response_completes_with_summary():
    jobs = process_jobs()
    job = find_job(jobs, "job-readable-malformed")

    assert job["status"] == "completed"
    assert job["summary"] == "Player improved timing"
    assert job["skill"] == "patience"
    assert job["retries"] == 0


def test_completed_job_remains_unchanged():
    expected = copy.deepcopy(find_job(BASE_JOBS, "job-already-complete"))

    jobs = process_jobs()

    assert find_job(jobs, "job-already-complete") == expected


def test_unusable_response_increments_retry_without_completion():
    job = find_job(process_jobs(), "job-empty-events")

    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert "summary" not in job


def test_unusable_response_fails_after_retry_limit():
    process_jobs()
    job = find_job(process_jobs(), "job-empty-events")

    assert job["status"] == "failed"
    assert job["retries"] == 2


def test_original_events_are_preserved():
    original_events = {job["id"]: copy.deepcopy(job["events"]) for job in BASE_JOBS}

    jobs = process_jobs()

    assert {job["id"]: job["events"] for job in jobs} == original_events


def test_repeated_runs_are_idempotent_for_completed_jobs():
    first_jobs = process_jobs()
    first_completed = copy.deepcopy(find_job(first_jobs, "job-readable-malformed"))

    second_jobs = process_jobs()

    assert find_job(second_jobs, "job-readable-malformed") == first_completed


def test_completed_jobs_always_have_non_empty_summaries():
    process_jobs()
    jobs = process_jobs()
    completed = [job for job in jobs if job["status"] == "completed"]

    assert completed
    assert all(isinstance(job.get("summary"), str) and job["summary"].strip() for job in completed)

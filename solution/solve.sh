#!/bin/bash
set -e

cat > /app/queue_manager.py <<'PY'
MAX_RETRIES = 2


def should_process(job):
    return job.get("status") in {"pending", "retrying"}


def mark_retry(job):
    job["retries"] = job.get("retries", 0) + 1
    if job["retries"] >= MAX_RETRIES:
        job["status"] = "failed"
    else:
        job["status"] = "retrying"
PY

cat > /app/pipeline.py <<'PY'
import json
import re

from model_client import generate_summary
from queue_manager import mark_retry, should_process
from storage import load_jobs, save_jobs

MIN_CONFIDENCE = 0.7


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def from_json_object(response):
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return None

    summary = data.get("summary") or data.get("summary_text") or data.get("text")
    skill = data.get("skill") or data.get("skill_tag") or data.get("skill_name")
    confidence = as_float(data.get("confidence", data.get("score")))

    if not summary or not skill or confidence is None:
        return None

    return {"summary": summary.strip(), "skill": skill.strip(), "confidence": confidence}


def read_field(response, names):
    for name in names:
        pattern = rf"(?:^|[|;])\s*{name}\s*[:=]\s*([^|;]+)"
        match = re.search(pattern, response, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def from_text_response(response):
    summary = read_field(response, ["summary"])
    skill = read_field(response, ["skill"])
    confidence = as_float(read_field(response, ["confidence", "score"]))

    if not summary or not skill or confidence is None:
        return None

    return {"summary": summary, "skill": skill, "confidence": confidence}


def parse_model_response(response):
    return from_json_object(response) or from_text_response(response)


def clear_partial_result(job):
    job.pop("summary", None)
    job.pop("skill", None)
    job.pop("confidence", None)


def complete_job(job, parsed):
    job["summary"] = parsed["summary"]
    job["skill"] = parsed["skill"]
    job["confidence"] = parsed["confidence"]
    job["status"] = "completed"


def process_jobs():
    jobs = load_jobs()

    for job in jobs:
        if not should_process(job):
            continue

        parsed = parse_model_response(generate_summary(job))

        if parsed and parsed["confidence"] >= MIN_CONFIDENCE:
            complete_job(job, parsed)
        else:
            clear_partial_result(job)
            mark_retry(job)

    save_jobs(jobs)
    return jobs
PY

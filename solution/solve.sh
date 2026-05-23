#!/bin/bash
set -euo pipefail

cat > /app/queue_manager.py <<'PY'
MAX_RETRIES = 2


def claimable_jobs(jobs):
    return [
        job
        for job in jobs
        if job.get("status") in {"pending", "retrying"}
        and job.get("retries", 0) < MAX_RETRIES
    ]


def record_retry(job):
    retries = job.get("retries", 0) + 1
    job["retries"] = retries
    job["status"] = "failed" if retries >= MAX_RETRIES else "retrying"
    job.pop("summary", None)


def mark_completed(job, summary):
    job["summary"] = summary
    job["status"] = "completed"
PY

cat > /app/pipeline.py <<'PY'
import json
import re

import model_client
import storage
from queue_manager import claimable_jobs, mark_completed, record_retry


def _split_loose_fields(response):
    fields = {}
    body = response.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]

    for part in body.split(","):
        key, separator, value = part.partition(":")
        if not separator:
            continue
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        if key and value:
            fields[key] = value
    return fields


def parse_model_response(response):
    try:
        parsed = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        parsed = _split_loose_fields(response) if isinstance(response, str) else {}

    if not isinstance(parsed, dict):
        return None

    summary = parsed.get("summary")
    if not isinstance(summary, str):
        return None

    summary = re.sub(r"\s+", " ", summary).strip()
    if not summary:
        return None

    skill = parsed.get("skill")
    return {
        "summary": summary,
        "skill": skill.strip() if isinstance(skill, str) and skill.strip() else None,
    }


def process_jobs(path=storage.DEFAULT_PATH):
    jobs = storage.load_jobs(path)

    for job in claimable_jobs(jobs):
        parsed = parse_model_response(model_client.summarize(job))
        if parsed is None:
            record_retry(job)
            continue

        mark_completed(job, parsed["summary"])
        if parsed["skill"]:
            job["skill"] = parsed["skill"]

    storage.save_jobs(jobs, path)
    return jobs
PY

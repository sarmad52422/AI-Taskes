import json

from model_client import generate_summary
from queue_manager import mark_retry, should_process
from storage import load_jobs, save_jobs


def parse_model_response(response):
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return None

    return {
        "summary": data.get("summary"),
        "skill": data.get("skill"),
        "confidence": float(data.get("confidence", 0)),
    }


def process_jobs():
    jobs = load_jobs()

    for job in jobs:
        if not should_process(job):
            continue

        parsed = parse_model_response(generate_summary(job))

        if parsed and parsed.get("summary"):
            job["summary"] = parsed["summary"]
            job["skill"] = parsed.get("skill") or "unknown"
            job["confidence"] = parsed.get("confidence", 0)
            job["status"] = "completed"
            continue

        job.pop("summary", None)
        job.pop("skill", None)
        job.pop("confidence", None)
        mark_retry(job)

    save_jobs(jobs)
    return jobs

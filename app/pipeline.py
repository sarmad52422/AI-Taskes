import json

from app import model_client, storage
from app.queue_manager import claimable_jobs, mark_completed, record_retry


def parse_model_response(response):
    parsed = json.loads(response)
    return parsed["summary"], parsed.get("skill")


def process_jobs(path=storage.DEFAULT_PATH):
    jobs = storage.load_jobs(path)

    for job in claimable_jobs(jobs):
        try:
            summary, skill = parse_model_response(model_client.summarize(job))
        except Exception:
            record_retry(job)
            continue

        mark_completed(job, summary)
        if skill:
            job["skill"] = skill

    storage.save_jobs(jobs, path)
    return jobs

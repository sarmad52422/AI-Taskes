MAX_RETRIES = 3


def claimable_jobs(jobs):
    return [
        job
        for job in jobs
        if job.get("status") in {"pending", "retrying", "completed"}
        and job.get("retries", 0) < MAX_RETRIES
    ]


def record_retry(job):
    retries = job.get("retries", 0) + 1
    job["retries"] = retries
    job["status"] = "retrying"


def mark_completed(job, summary):
    job["summary"] = summary
    job["status"] = "completed"

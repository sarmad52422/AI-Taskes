MAX_RETRIES = 2


def should_process(job):
    return job.get("status") != "failed"


def mark_retry(job):
    job["retries"] = job.get("retries", 0) + 1
    if job["retries"] > MAX_RETRIES:
        job["status"] = "failed"
    else:
        job["status"] = "retrying"

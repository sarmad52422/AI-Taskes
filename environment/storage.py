import json
from pathlib import Path

DATA_PATH = Path("/app/jobs.json")


def load_jobs():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def save_jobs(jobs):
    DATA_PATH.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")

import json
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "jobs.json"


def load_jobs(path=DEFAULT_PATH):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def save_jobs(jobs, path=DEFAULT_PATH):
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(jobs, handle, indent=2)
        handle.write("\n")

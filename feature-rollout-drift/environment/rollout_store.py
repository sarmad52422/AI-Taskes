import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "rollouts.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "rollouts.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def eligible_users(document, segment):
    record = document.setdefault("segments", {}).setdefault(segment, {})
    return list(record.setdefault("eligible_users", []))


def assignment_record(document, feature_key, segment):
    feature = document.setdefault("assignments", {}).setdefault(feature_key, {})
    return feature.setdefault(segment, {"assigned_count": 0, "users": {}})


def assigned_users(document, feature_key, segment):
    return assignment_record(document, feature_key, segment).setdefault("users", {})


def available_users(document, job):
    users = (
        document.get("assignments", {})
        .get(job["feature_key"], {})
        .get(job["segment"], {})
        .get("users", {})
    )
    return [user_id for user_id in eligible_users(document, job["segment"]) if user_id not in users]


def assignment_id(job, user_id):
    return f"{job['rollout_id']}:{user_id}"


def assign_users(document, job):
    target = int(job["target_count"])
    available = available_users(document, job)
    if len(available) < target:
        return False

    record = assignment_record(document, job["feature_key"], job["segment"])
    users = record.setdefault("users", {})
    for user_id in available[:target]:
        current_id = assignment_id(job, user_id)
        existing = users.get(user_id, {})
        users[user_id] = {
            "assignment_id": current_id,
            "rollout_id": job["rollout_id"],
            "status": "active",
            "write_count": int(existing.get("write_count", 0)) + 1,
        }
        record["assigned_count"] = int(record.get("assigned_count", 0)) + 1
        job.setdefault("assignment_ids", []).append(current_id)
    return True

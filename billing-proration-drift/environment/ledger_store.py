import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "events.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "events.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def period_record(document, customer_id, period):
    account = document.setdefault("accounts", {}).setdefault(customer_id, {})
    periods = account.setdefault("periods", {})
    return periods.setdefault(period, {"open": True, "remaining_cents": 0})


def ledger_record(document, customer_id):
    return document.setdefault("ledger", {}).setdefault(customer_id, {"entries": {}})


def remaining_cents(document, job):
    period = period_record(document, job["customer_id"], job["period"])
    if not period.get("open", True):
        return 0
    return int(period.get("remaining_cents", 0))


def ledger_entry_id(job):
    return f"{job['adjustment_id']}:{job['period']}"


def post_adjustment(document, job):
    period = period_record(document, job["customer_id"], job["period"])
    if not period.get("open", True):
        return False

    amount = int(job["amount_cents"])
    remaining = int(period.get("remaining_cents", 0))
    if remaining < amount:
        return False

    entry_id = ledger_entry_id(job)
    period["remaining_cents"] = remaining - amount
    ledger = ledger_record(document, job["customer_id"])
    entries = ledger.setdefault("entries", {})
    existing = entries.get(entry_id, {})
    entries[entry_id] = {
        "adjustment_id": job["adjustment_id"],
        "amount_cents": int(existing.get("amount_cents", 0)) + amount,
        "period": job["period"],
        "posted_by": "proration-worker",
        "write_count": int(existing.get("write_count", 0)) + 1,
    }
    job.setdefault("ledger_entry_ids", []).append(entry_id)
    return True

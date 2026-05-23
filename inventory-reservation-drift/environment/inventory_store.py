import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "jobs.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "jobs.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def inventory(document):
    return document.setdefault("inventory", {})


def item_record(document, warehouse, item_id):
    warehouse_items = inventory(document).setdefault(warehouse, {})
    return warehouse_items.setdefault(item_id, {"available": 0, "reserved": {}})


def available_quantity(document, job):
    item = item_record(document, job["warehouse"], job["item_id"])
    return int(item.get("available", 0))


def reserve_stock(document, job):
    item = item_record(document, job["warehouse"], job["item_id"])
    quantity = int(job["quantity"])
    available = int(item.get("available", 0))
    if available < quantity:
        return False

    item["available"] = available - quantity
    reserved = item.setdefault("reserved", {})
    reservation_id = job["reservation_id"]
    reserved[reservation_id] = int(reserved.get(reservation_id, 0)) + quantity
    job.setdefault("reservation_ids", []).append(reservation_id)
    return True

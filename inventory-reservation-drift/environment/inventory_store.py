import json
from pathlib import Path


def load_json(path):
    with Path(path).open() as handle:
        return json.load(handle)


def save_json(path, payload):
    with Path(path).open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_jobs(app_dir):
    return load_json(Path(app_dir) / "jobs.json")


def write_jobs(app_dir, jobs):
    save_json(Path(app_dir) / "jobs.json", jobs)


def read_inventory(app_dir):
    return load_json(Path(app_dir) / "inventory.json")


def write_inventory(app_dir, inventory):
    save_json(Path(app_dir) / "inventory.json", inventory)


def item_record(inventory, warehouse, item_id):
    warehouse_items = inventory.setdefault(warehouse, {})
    return warehouse_items.setdefault(item_id, {"available": 0, "reserved": {}})


def available_quantity(inventory, job):
    item = item_record(inventory, job["warehouse"], job["item_id"])
    return int(item.get("available", 0))


def reserve_stock(inventory, job):
    item = item_record(inventory, job["warehouse"], job["item_id"])
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

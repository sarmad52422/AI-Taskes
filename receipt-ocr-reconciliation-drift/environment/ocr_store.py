import copy
import json
from pathlib import Path


DATA_PATH = Path("/app/receipts.json")


def load_document(path=DATA_PATH):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def receipt_bucket(document, receipt_id):
    index = document.setdefault("reconciliation_index", {})
    bucket = index.setdefault(receipt_id, {})
    bucket.setdefault("fields", {})
    bucket.setdefault("reconciled_count", 0)
    return bucket


def ocr_pages_for(document, receipt_id):
    return document.get("ocr_pages", {}).get(receipt_id, {})


def find_field(document, job, field_key):
    pages = ocr_pages_for(document, job["receipt_id"])
    for page_id in job.get("page_ids", []):
        page = pages.get(page_id)
        if not page:
            continue
        for field in page.get("fields", []):
            if field.get("key") == field_key:
                found = copy.deepcopy(field)
                found["page_id"] = page_id
                found["checksum"] = page.get("checksum")
                return found
    return None


def cached_field(document, job, field_key):
    bucket = receipt_bucket(document, job["receipt_id"])
    return bucket["fields"].get(field_key)


def field_id_for(job, field_key):
    return f"{job['job_id']}:{field_key}"


def write_field(document, job, field_key, field):
    bucket = receipt_bucket(document, job["receipt_id"])
    previous = bucket["fields"].get(field_key, {})
    record = {
        "field_id": field_id_for(job, field_key),
        "job_id": job["job_id"],
        "page_id": field["page_id"],
        "source_checksum": field["checksum"],
        "raw_value": field.get("raw_value", ""),
        "normalized_value": field.get("normalized_value", field.get("raw_value", "")),
        "confidence": field.get("confidence", 0),
        "write_count": previous.get("write_count", 0) + 1,
    }
    bucket["fields"][field_key] = record
    bucket["reconciled_count"] = bucket.get("reconciled_count", 0) + 1
    return record

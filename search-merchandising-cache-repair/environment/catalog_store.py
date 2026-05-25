import copy
import json
from pathlib import Path


CATALOG_PATH = Path("/app/catalog.json")


def load_catalog(path=CATALOG_PATH):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_catalog(document, path=CATALOG_PATH):
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def eligible_products(document, search):
    blocked = set(search.get("blocked_ids", []))
    category = search.get("category")
    products = []
    for product in document.get("products", []):
        if category and product.get("category") != category:
            continue
        if product.get("product_id") in blocked:
            continue
        products.append(copy.deepcopy(product))
    return products


def existing_result(document, query_id):
    return document.setdefault("search_results", {}).get(query_id)


def write_result(document, query_id, result):
    document.setdefault("search_results", {})[query_id] = result


def write_conflict(document, query_id, conflict):
    document.setdefault("result_conflicts", {})[query_id] = conflict


def clear_conflict(document, query_id):
    document.setdefault("result_conflicts", {}).pop(query_id, None)

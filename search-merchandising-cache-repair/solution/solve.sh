#!/bin/bash
set -e

cat > /app/catalog_store.py <<'PY'
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
        if not product.get("in_stock", False):
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
PY

cat > /app/ranking_rules.py <<'PY'
from datetime import datetime, timezone


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def active_policy(document, search):
    now = parse_time(document["generated_at"])
    active = []
    for experiment in document.get("experiments", []):
        if experiment.get("query_id") != search.get("query_id"):
            continue
        start = parse_time(experiment["active_from"])
        end = parse_time(experiment["active_until"])
        if start <= now < end:
            active.append((start, experiment))
    if active:
        active.sort(key=lambda item: item[0], reverse=True)
        experiment = active[0][1]
        return {
            "name": experiment["name"],
            "owner": experiment.get("owner", "search"),
            "weights": dict(experiment.get("weights", {})),
        }
    return {
        "name": "default",
        "owner": "search",
        "weights": dict(document.get("default_weights", {})),
    }


def freshness(product):
    age = min(max(product.get("recency_days", 30), 0), 30)
    return 1 - (age / 30)


def score_product(product, weights, search):
    score = 0
    score += product.get("base_score", 0) * weights.get("relevance", 0)
    score += product.get("ctr", 0) * weights.get("ctr", 0)
    score += product.get("margin", 0) * weights.get("margin", 0)
    score += freshness(product) * weights.get("freshness", 0)
    score += search.get("brand_boosts", {}).get(product.get("brand"), 0)
    if product.get("product_id") in set(search.get("demoted_ids", [])):
        score -= 0.35
    return round(score, 4)


def sorted_products(scored):
    return sorted(scored, key=lambda item: (-item[1], item[0]["product_id"]))


def manual_value(value):
    if isinstance(value, dict):
        return value.get("product_id"), value.get("owner")
    return value, "editorial"


def valid_manual_slots(manual_slots, eligible_ids, page_size):
    kept = {}
    seen_products = set()
    seen_slots = set()
    for slot, value in manual_slots.items():
        product_id, owner = manual_value(value)
        try:
            position = int(slot)
        except (TypeError, ValueError):
            continue
        if position < 1 or position > page_size:
            continue
        if owner != "editorial" or product_id not in eligible_ids:
            continue
        if product_id in seen_products or position in seen_slots:
            continue
        kept[str(position)] = {"product_id": product_id, "owner": owner}
        seen_products.add(product_id)
        seen_slots.add(position)
    return kept


def merge_slots(ranked_ids, manual_slots, page_size):
    output = []
    by_slot = {int(slot): value["product_id"] for slot, value in manual_slots.items()}
    manual_products = set(by_slot.values())
    fill = [product_id for product_id in ranked_ids if product_id not in manual_products]
    used = set()
    for position in range(1, page_size + 1):
        if position in by_slot and by_slot[position] not in used:
            output.append(by_slot[position])
            used.add(by_slot[position])
        else:
            while fill and fill[0] in used:
                fill.pop(0)
            if fill:
                product_id = fill.pop(0)
                output.append(product_id)
                used.add(product_id)
    return output
PY

cat > /app/search_reconciler.py <<'PY'
import copy

from catalog_store import clear_conflict, eligible_products, existing_result, load_catalog, save_catalog, write_conflict, write_result
from ranking_rules import active_policy, merge_slots, score_product, sorted_products, valid_manual_slots


MAX_ATTEMPTS = 3


def is_fresh_active_row(row, product_id, owner, score, position, generated_at, experiment_name, catalog_version):
    return (
        row.get("product_id") == product_id
        and row.get("owner") == owner
        and row.get("score") == score
        and row.get("position") == position
        and row.get("generated_at") == generated_at
        and row.get("experiment_name") == experiment_name
        and row.get("manual") is False
        and row.get("catalog_version") == catalog_version
    )


def is_protected_manual_row(row, product_id, position):
    return (
        row.get("product_id") == product_id
        and row.get("owner") == "editorial"
        and row.get("manual") is True
        and row.get("position") == position
    )


def conflict_result(current, query_id, policy, protected_ids, generated_at):
    result = copy.deepcopy(current)
    attempts = result.get("attempts", 0)
    exhausted = attempts >= MAX_ATTEMPTS
    result["status"] = "failed" if exhausted else "retrying"
    result["attempts"] = attempts if exhausted else attempts + 1
    result["failure_reason"] = "protected_cache_conflict"
    result["review_required"] = True
    result["conflict"] = {
        "query_id": query_id,
        "reason": "protected_cache_conflict",
        "active_owner": policy["owner"],
        "protected_product_ids": protected_ids,
        "generated_at": generated_at,
    }
    return result


def build_row(previous, product_id, owner, score, position, generated_at, experiment_name, catalog_version):
    return {
        "product_id": product_id,
        "owner": owner,
        "score": score,
        "position": position,
        "generated_at": generated_at,
        "experiment_name": experiment_name,
        "write_count": previous.get("write_count", 0) + 1,
        "manual": False,
        "catalog_version": catalog_version,
    }


def reconcile_search(document, search):
    query_id = search["query_id"]
    current = existing_result(document, query_id) or {}
    if current.get("frozen"):
        return

    policy = active_policy(document, search)
    generated_at = document.get("generated_at")
    products = eligible_products(document, search)
    scored = [(product, score_product(product, policy["weights"], search)) for product in products]
    ranked = sorted_products(scored)
    ranked_ids = [product["product_id"] for product, score in ranked]
    score_lookup = {product["product_id"]: score for product, score in ranked}
    version_lookup = {product["product_id"]: product.get("catalog_version") for product, score in ranked}
    page_size = search.get("page_size", len(ranked_ids))
    manual_slots = valid_manual_slots(current.get("manual_slots", {}), set(ranked_ids), page_size)
    product_ids = merge_slots(ranked_ids, manual_slots, page_size)
    manual_by_product = {value["product_id"]: int(slot) for slot, value in manual_slots.items()}
    previous_rows = current.get("rows", {})

    protected = []
    for position, product_id in enumerate(product_ids, start=1):
        previous = previous_rows.get(product_id)
        if not previous:
            continue
        if product_id in manual_by_product and is_protected_manual_row(previous, product_id, position):
            continue
        if previous.get("owner") not in (None, policy["owner"]):
            protected.append(product_id)
    if protected:
        blocked = conflict_result(current, query_id, policy, protected, generated_at)
        write_result(document, query_id, blocked)
        write_conflict(document, query_id, blocked["conflict"])
        return

    rows = {}
    for position, product_id in enumerate(product_ids, start=1):
        previous = previous_rows.get(product_id, {})
        score = score_lookup[product_id]
        if product_id in manual_by_product:
            if is_protected_manual_row(previous, product_id, position):
                rows[product_id] = copy.deepcopy(previous)
            else:
                rows[product_id] = {
                    "product_id": product_id,
                    "owner": "editorial",
                    "score": previous.get("score", score),
                    "position": position,
                    "generated_at": previous.get("generated_at", generated_at),
                    "experiment_name": previous.get("experiment_name", policy["name"]),
                    "write_count": previous.get("write_count", 0) + 1,
                    "manual": True,
                }
            continue
        catalog_version = version_lookup[product_id]
        if previous and is_fresh_active_row(previous, product_id, policy["owner"], score, position, generated_at, policy["name"], catalog_version):
            rows[product_id] = copy.deepcopy(previous)
        else:
            rows[product_id] = build_row(previous, product_id, policy["owner"], score, position, generated_at, policy["name"], catalog_version)

    result = {
        "query_id": query_id,
        "status": "ready",
        "attempts": current.get("attempts", 0),
        "generated_at": generated_at,
        "experiment_name": policy["name"],
        "owner": policy["owner"],
        "product_ids": product_ids,
        "scores": {product_id: score_lookup[product_id] for product_id in product_ids},
        "applied_weights": policy["weights"],
        "manual_slots": manual_slots,
        "rows": rows,
        "history": list(current.get("history", [])),
    }
    write_result(document, query_id, result)
    clear_conflict(document, query_id)


def run(path="/app/catalog.json"):
    document = load_catalog(path)
    for search in document.get("searches", []):
        reconcile_search(document, search)
    save_catalog(document, path)
    return document


if __name__ == "__main__":
    run()
PY

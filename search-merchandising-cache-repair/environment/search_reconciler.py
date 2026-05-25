from catalog_store import clear_conflict, eligible_products, existing_result, load_catalog, save_catalog, write_result
from ranking_rules import active_policy, merge_slots, score_product, sorted_products


def reconcile_search(document, search):
    query_id = search["query_id"]
    current = existing_result(document, query_id) or {}
    if current.get("frozen"):
        return

    policy = active_policy(document, search)
    products = eligible_products(document, search)
    scored = [(product, score_product(product, policy["weights"], search)) for product in products]
    ranked = sorted_products(scored)
    ranked_ids = [product["product_id"] for product, score in ranked]
    score_lookup = {product["product_id"]: score for product, score in ranked}
    page_size = search.get("page_size", len(ranked_ids))
    manual_slots = dict(current.get("manual_slots", {}))
    product_ids = merge_slots(ranked_ids, manual_slots, page_size)
    previous_rows = current.get("rows", {})
    rows = {}
    for position, product_id in enumerate(product_ids, start=1):
        previous = previous_rows.get(product_id, {})
        rows[product_id] = {
            "product_id": product_id,
            "owner": policy["owner"],
            "score": score_lookup.get(product_id, previous.get("score", 0)),
            "position": position,
            "generated_at": document.get("generated_at"),
            "experiment_name": policy["name"],
            "write_count": previous.get("write_count", 0) + 1,
            "manual": product_id not in ranked_ids,
        }
    result = {
        "query_id": query_id,
        "status": "ready",
        "attempts": current.get("attempts", 0),
        "generated_at": document.get("generated_at"),
        "experiment_name": policy["name"],
        "owner": policy["owner"],
        "product_ids": list(product_ids),
        "scores": {product_id: score_lookup.get(product_id, rows[product_id]["score"]) for product_id in product_ids},
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

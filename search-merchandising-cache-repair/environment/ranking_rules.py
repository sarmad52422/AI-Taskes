from datetime import datetime, timezone


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def active_policy(document, search):
    return {
        "name": "default",
        "owner": "search",
        "weights": document.get("default_weights", {}),
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
    return round(score, 4)


def sorted_products(scored):
    return sorted(scored, key=lambda item: (-item[1], item[0]["product_id"]))


def manual_product(value):
    if isinstance(value, dict):
        return value.get("product_id")
    return value


def merge_slots(ranked_ids, manual_slots, page_size):
    output = []
    used = set()
    by_slot = {}
    for slot, value in manual_slots.items():
        product_id = manual_product(value)
        try:
            position = int(slot)
        except ValueError:
            continue
        if position < 1 or position > page_size or product_id in used:
            continue
        by_slot[position] = product_id
        used.add(product_id)
    fill = [product_id for product_id in ranked_ids if product_id not in used]
    for position in range(1, page_size + 1):
        if position in by_slot:
            output.append(by_slot[position])
        elif fill:
            output.append(fill.pop(0))
    return output

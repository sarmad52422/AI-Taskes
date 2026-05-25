import copy
import importlib
import json
import sys
from pathlib import Path


APP = Path("/app")
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


DEFAULT_WEIGHTS = {"relevance": 0.55, "ctr": 0.25, "margin": 0.1, "freshness": 0.1}
MARGIN_WEIGHTS = {"relevance": 0.35, "ctr": 0.2, "margin": 0.35, "freshness": 0.1}


def product(product_id, category="shoes", brand="north", stock=True, base=0.6, ctr=0.2, margin=0.4, age=5, catalog_version=None):
    return {
        "product_id": product_id,
        "title": product_id.upper(),
        "category": category,
        "brand": brand,
        "in_stock": stock,
        "base_score": base,
        "ctr": ctr,
        "margin": margin,
        "recency_days": age,
        "catalog_version": catalog_version or f"{product_id}-2026-03",
    }


def products():
    return [
        product("p1", brand="north", base=0.72, ctr=0.2, margin=0.55, age=3),
        product("p2", brand="acme", base=0.7, ctr=0.32, margin=0.35, age=10),
        product("p3", brand="acme", stock=False, base=0.95, ctr=0.5, margin=0.9, age=1),
        product("p4", brand="orbit", base=0.64, ctr=0.42, margin=0.2, age=1),
        product("p5", category="bags", brand="north", base=0.8, ctr=0.18, margin=0.6, age=5),
        product("p7", brand="sol", base=0.55, ctr=0.22, margin=0.8, age=2),
    ]


def experiment(name="margin-shoes", query_id="q-shoes", owner="growth", active_from="2026-03-01T00:00:00Z", active_until="2026-04-01T00:00:00Z", weights=None):
    return {
        "name": name,
        "query_id": query_id,
        "owner": owner,
        "active_from": active_from,
        "active_until": active_until,
        "weights": copy.deepcopy(weights or MARGIN_WEIGHTS),
    }


def search(**overrides):
    data = {
        "query_id": "q-shoes",
        "search_text": "walking shoes",
        "category": "shoes",
        "page_size": 4,
        "blocked_ids": [],
        "demoted_ids": [],
        "brand_boosts": {},
    }
    data.update(overrides)
    return data


def row(product_id, owner="growth", score=0.0, position=1, generated_at="2026-03-08T10:00:00Z", experiment_name="margin-shoes", write_count=1, manual=False, catalog_version=None):
    return {
        "product_id": product_id,
        "owner": owner,
        "score": score,
        "position": position,
        "generated_at": generated_at,
        "experiment_name": experiment_name,
        "write_count": write_count,
        "manual": manual,
        "catalog_version": catalog_version or f"{product_id}-2026-03",
    }


def cached_result(**overrides):
    data = {
        "query_id": "q-shoes",
        "status": "ready",
        "attempts": 0,
        "generated_at": "2026-03-01T00:00:00Z",
        "experiment_name": "old-exp",
        "owner": "growth",
        "product_ids": ["p2", "p1", "p4"],
        "scores": {"p2": 0.5, "p1": 0.4, "p4": 0.3},
        "applied_weights": copy.deepcopy(DEFAULT_WEIGHTS),
        "manual_slots": {},
        "rows": {},
        "history": [],
    }
    data.update(overrides)
    return data


def document(searches=None, experiments=None, results=None, conflicts=None, generated_at="2026-03-08T10:00:00Z", product_list=None):
    return {
        "generated_at": generated_at,
        "default_weights": copy.deepcopy(DEFAULT_WEIGHTS),
        "experiments": copy.deepcopy(experiments if experiments is not None else [experiment()]),
        "products": copy.deepcopy(product_list if product_list is not None else products()),
        "searches": copy.deepcopy(searches if searches is not None else [search()]),
        "search_results": copy.deepcopy(results or {}),
        "result_conflicts": copy.deepcopy(conflicts or {}),
    }


def run_worker(tmp_path, payload):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    worker = importlib.reload(importlib.import_module("search_reconciler"))
    returned = worker.run(str(path))
    persisted = json.loads(path.read_text(encoding="utf-8"))
    return returned, persisted


def result(doc, query_id="q-shoes"):
    return doc["search_results"][query_id]


def test_persisted_document_is_returned(tmp_path):
    returned, persisted = run_worker(tmp_path, document())
    assert returned == persisted


def test_refresh_page_order_and_cleanup(tmp_path):
    rows = {
        "p7": row("p7", score=0.6098, position=1, write_count=5),
        "p3": row("p3", score=0.99, position=3, write_count=4),
        "ghost": row("ghost", score=0.5, position=4, write_count=6),
    }
    cached = cached_result(product_ids=["p1", "p1", "p7", "p3", "ghost"], rows=rows)
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    page = result(returned)
    saved_rows = result(persisted)["rows"]
    assert page["product_ids"] == ["p7", "p1", "p2", "p4"]
    assert len(page["product_ids"]) == len(set(page["product_ids"]))
    assert "p3" not in saved_rows
    assert "ghost" not in saved_rows


def test_refresh_reuses_and_updates_rows(tmp_path):
    rows = {
        "p7": row("p7", score=0.6098, position=1, write_count=5),
        "p1": row("p1", score=0.1111, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="old-exp", write_count=2),
    }
    cached = cached_result(product_ids=["p7", "p1"], rows=rows)
    returned, _ = run_worker(tmp_path, document(results={"q-shoes": cached}))
    page = result(returned)
    assert page["rows"]["p7"]["write_count"] == 5
    assert page["rows"]["p1"]["write_count"] == 3
    assert page["rows"]["p1"]["score"] == 0.5745
    assert page["rows"]["p2"]["catalog_version"] == "p2-2026-03"


def test_protected_rows_record_review_state(tmp_path):
    protected = row("p1", owner="editorial", score=0.4411, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="editorial", write_count=5)
    cached = cached_result(product_ids=["p1", "p7"], rows={"p1": protected}, attempts=1)
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    blocked = result(returned)
    assert blocked["status"] == "retrying"
    assert blocked["attempts"] == 2
    assert blocked["failure_reason"] == "protected_cache_conflict"
    assert blocked["review_required"] is True
    assert blocked["conflict"]["protected_product_ids"] == ["p1"]
    assert returned["result_conflicts"]["q-shoes"] == blocked["conflict"]
    assert result(persisted)["rows"]["p1"] == protected


def test_protected_rows_prevent_partial_refresh(tmp_path):
    protected = row("p1", owner="editorial", score=0.4411, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="editorial", write_count=5)
    stale_active = row("p7", owner="growth", score=0.01, position=4, generated_at="2026-02-01T00:00:00Z", experiment_name="old-exp", write_count=4)
    cached = cached_result(product_ids=["p1", "p7"], rows={"p1": protected, "p7": stale_active})
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    blocked = result(returned)
    assert blocked["status"] == "retrying"
    assert blocked["rows"] == {"p1": protected, "p7": stale_active}
    assert result(persisted)["rows"] == {"p1": protected, "p7": stale_active}


def test_protected_limit_path(tmp_path):
    protected = row("p7", owner="system", score=0.1111, position=1, generated_at="2026-02-01T00:00:00Z", experiment_name="baseline", write_count=3)
    cached = cached_result(product_ids=["p7"], rows={"p7": protected}, attempts=3)
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    failed = result(returned)
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3
    assert failed["failure_reason"] == "protected_cache_conflict"
    assert result(persisted)["rows"]["p7"] == protected


def test_manual_slot_reconciliation(tmp_path):
    manual = {"2": {"product_id": "p4", "owner": "editorial"}}
    rows = {"p4": row("p4", owner="editorial", score=0.2222, position=2, write_count=8, manual=True)}
    first, _ = run_worker(tmp_path, document(results={"q-shoes": cached_result(product_ids=["p4"], manual_slots=manual, rows=rows)}))
    second, _ = run_worker(tmp_path, first)
    preserved = result(second)
    assert preserved["product_ids"] == ["p7", "p4", "p1", "p2"]
    assert preserved["manual_slots"] == manual
    assert preserved["rows"]["p4"] == rows["p4"]


def test_manual_slot_change_requires_review(tmp_path):
    manual = {"1": {"product_id": "p4", "owner": "editorial"}}
    protected = row("p4", owner="editorial", score=0.2222, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="editorial", write_count=8, manual=True)
    cached = cached_result(product_ids=["p7", "p4", "p1"], manual_slots=manual, rows={"p4": protected})
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    blocked = result(returned)
    assert blocked["status"] == "retrying"
    assert blocked["conflict"]["protected_product_ids"] == ["p4"]
    assert blocked["product_ids"] == ["p7", "p4", "p1"]
    assert result(persisted)["rows"]["p4"] == protected


def test_stale_owner_rows_outside_page_do_not_block(tmp_path):
    rows = {
        "p5": row("p5", owner="editorial", score=0.9, position=1, generated_at="2026-02-01T00:00:00Z", experiment_name="editorial", write_count=6),
        "p3": row("p3", owner="system", score=0.8, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="baseline", write_count=4),
    }
    cached = cached_result(product_ids=["p5", "p3", "p1"], rows=rows)
    returned, persisted = run_worker(tmp_path, document(results={"q-shoes": cached}))
    refreshed = result(returned)
    assert refreshed["status"] == "ready"
    assert refreshed["product_ids"] == ["p7", "p1", "p2", "p4"]
    assert "q-shoes" not in returned["result_conflicts"]
    assert "p5" not in result(persisted)["rows"]
    assert "p3" not in result(persisted)["rows"]


def test_conflict_list_uses_page_order(tmp_path):
    rows = {
        "p1": row("p1", owner="editorial", score=0.4411, position=2, generated_at="2026-02-01T00:00:00Z", experiment_name="editorial", write_count=5),
        "p7": row("p7", owner="system", score=0.1122, position=1, generated_at="2026-02-01T00:00:00Z", experiment_name="baseline", write_count=3),
    }
    returned, _ = run_worker(tmp_path, document(results={"q-shoes": cached_result(product_ids=["p1", "p7"], rows=rows)}))
    conflict = result(returned)["conflict"]
    assert conflict["protected_product_ids"] == ["p7", "p1"]
    assert returned["result_conflicts"]["q-shoes"]["protected_product_ids"] == ["p7", "p1"]


def test_revision_refresh_and_rerun_stability(tmp_path):
    stale = row("p7", score=0.6098, position=1, write_count=5, catalog_version="p7-2026-02")
    first, _ = run_worker(tmp_path, document(results={"q-shoes": cached_result(product_ids=["p7"], rows={"p7": stale})}))
    refreshed = result(first)["rows"]["p7"]
    assert refreshed["write_count"] == 6
    assert refreshed["catalog_version"] == "p7-2026-03"
    before = {pid: record["write_count"] for pid, record in result(first)["rows"].items()}
    second, _ = run_worker(tmp_path, first)
    after = {pid: record["write_count"] for pid, record in result(second)["rows"].items()}
    assert before == after


def test_success_path_cleans_review_state(tmp_path):
    cached = cached_result(status="retrying", attempts=2, failure_reason="protected_cache_conflict", review_required=True, conflict={"reason": "protected_cache_conflict"})
    returned, _ = run_worker(tmp_path, document(results={"q-shoes": cached}, conflicts={"q-shoes": {"reason": "protected_cache_conflict"}}))
    refreshed = result(returned)
    assert refreshed["status"] == "ready"
    assert "failure_reason" not in refreshed
    assert "review_required" not in refreshed
    assert "conflict" not in refreshed
    assert "q-shoes" not in returned["result_conflicts"]


def test_invalid_manual_hints_are_pruned(tmp_path):
    manual = {"1": {"product_id": "p4", "owner": "growth"}, "2": {"product_id": "p3", "owner": "editorial"}}
    cached = cached_result(product_ids=["p4", "p3", "p1"], manual_slots=manual, rows={"p4": row("p4", owner="growth", score=0.4747, position=1, write_count=2)})
    returned, _ = run_worker(tmp_path, document(results={"q-shoes": cached}))
    refreshed = result(returned)
    assert refreshed["manual_slots"] == {}
    assert refreshed["product_ids"] == ["p7", "p1", "p2", "p4"]
    assert refreshed["rows"]["p4"]["position"] == 4
    assert refreshed["rows"]["p4"]["write_count"] == 3


def test_blocked_manual_page_refills(tmp_path):
    manual = {"1": {"product_id": "p4", "owner": "editorial"}}
    rows = {"p4": row("p4", owner="editorial", score=0.3, position=1, write_count=7, manual=True)}
    cached = cached_result(product_ids=["p4", "p7"], manual_slots=manual, rows=rows)
    returned, _ = run_worker(tmp_path, document(searches=[search(blocked_ids=["p4"])], results={"q-shoes": cached}))
    refreshed = result(returned)
    assert refreshed["product_ids"] == ["p7", "p1", "p2"]
    assert refreshed["manual_slots"] == {}
    assert "p4" not in refreshed["rows"]

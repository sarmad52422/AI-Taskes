import copy
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, '/app')

DATA_PATH = Path('/app/stock_holds.json')


def write_doc(doc):
    DATA_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_doc():
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


def run_worker(doc):
    write_doc(copy.deepcopy(doc))
    import hold_worker
    importlib.reload(hold_worker)
    result = hold_worker.run()
    persisted = read_doc()
    return result, persisted


def base_doc():
    return {
        'inventory': {
            'amox-500': {'on_hand': 20},
            'para-500': {'on_hand': 50},
            'ator-10': {'on_hand': 8},
            'insulin-pen': {'on_hand': 3},
            'rare-drop': {'on_hand': 1},
        },
        'orders': [],
        'holds': [],
        'stats': {'holds_created': 0, 'holds_refreshed': 0, 'conflicts': 0, 'failures': 0},
    }


def order(order_id, status='queued', items=None, attempts=0, max_attempts=2, metadata=None):
    return {
        'id': order_id,
        'status': status,
        'items': items or [{'medicine_id': 'amox-500', 'qty': 2}],
        'attempts': attempts,
        'max_attempts': max_attempts,
        'metadata': metadata or {},
    }


def hold(order_id, medicine_id, qty, owner='hold_worker', state='active', source_attempt=0):
    return {
        'order_id': order_id,
        'medicine_id': medicine_id,
        'qty': qty,
        'owner': owner,
        'state': state,
        'source_attempt': source_attempt,
    }


def holds_for(doc, order_id):
    return [h for h in doc['holds'] if h['order_id'] == order_id]


def test_worker_return_value_equals_persisted_json():
    doc = base_doc()
    doc['orders'] = [order('rx-a')]
    result, persisted = run_worker(doc)
    assert result == persisted


def test_happy_path_creates_all_requested_holds_and_marks_held():
    doc = base_doc()
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'amox-500', 'qty': 2}, {'medicine_id': 'para-500', 'qty': 3}])]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'held'
    assert updated['attempts'] == 0
    assert sorted((h['medicine_id'], h['qty'], h['owner'], h['state']) for h in holds_for(persisted, 'rx-a')) == [
        ('amox-500', 2, 'hold_worker', 'active'),
        ('para-500', 3, 'hold_worker', 'active'),
    ]
    assert persisted['stats']['holds_created'] == 2


def test_missing_inventory_retries_without_partial_writes():
    doc = base_doc()
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'amox-500', 'qty': 2}, {'medicine_id': 'rare-drop', 'qty': 2}], attempts=0, max_attempts=3)]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'stock_unavailable'
    assert holds_for(persisted, 'rx-a') == []
    assert persisted['stats']['failures'] == 0


def test_retrying_job_recovers_when_stock_becomes_complete_and_clears_stale_metadata():
    doc = base_doc()
    doc['inventory']['rare-drop']['on_hand'] = 4
    doc['orders'] = [order('rx-a', status='retrying', items=[{'medicine_id': 'rare-drop', 'qty': 2}], attempts=1, max_attempts=3, metadata={'retry_reason': 'stock_unavailable', 'last_error': 'short pick', 'failed_at': 'old', 'channel': 'mobile'})]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'held'
    assert updated['attempts'] == 1
    assert updated['metadata'] == {'channel': 'mobile'}
    assert holds_for(persisted, 'rx-a')[0]['qty'] == 2


def test_exhausted_missing_inventory_moves_to_failed_and_counts_failure_once():
    doc = base_doc()
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'rare-drop', 'qty': 5}], attempts=1, max_attempts=2)]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'stock_unavailable'
    assert persisted['stats']['failures'] == 1
    assert holds_for(persisted, 'rx-a') == []


def test_terminal_held_order_and_its_hold_are_unchanged():
    doc = base_doc()
    terminal = order('rx-done', status='held', items=[{'medicine_id': 'insulin-pen', 'qty': 1}], metadata={'packed_by': 'ali'})
    terminal_hold = hold('rx-done', 'insulin-pen', 1, source_attempt=0)
    doc['orders'] = [terminal]
    doc['holds'] = [terminal_hold]
    _, persisted = run_worker(doc)
    assert persisted['orders'][0] == terminal
    assert persisted['holds'][0] == terminal_hold
    assert persisted['stats'] == doc['stats']


def test_terminal_failed_and_cancelled_orders_are_unchanged():
    doc = base_doc()
    failed = order('rx-failed', status='failed', attempts=2, max_attempts=2, metadata={'retry_reason': 'stock_conflict', 'failed_at': '2026-05-01'})
    cancelled = order('rx-cancel', status='cancelled', metadata={'cancelled_by': 'customer'})
    doc['orders'] = [failed, cancelled]
    _, persisted = run_worker(doc)
    assert persisted['orders'] == [failed, cancelled]
    assert persisted['holds'] == []


def test_repeated_runs_do_not_duplicate_holds_or_inflate_counters():
    doc = base_doc()
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'amox-500', 'qty': 2}])]
    first, _ = run_worker(doc)
    second, persisted = run_worker(first)
    assert second == persisted
    assert len(holds_for(persisted, 'rx-a')) == 1
    assert persisted['stats']['holds_created'] == 1
    assert persisted['stats']['holds_refreshed'] == 0


def test_existing_matching_partial_hold_is_reused_without_counter_increase():
    doc = base_doc()
    doc['orders'] = [order('rx-a', status='holding', items=[{'medicine_id': 'amox-500', 'qty': 2}], attempts=1)]
    existing = hold('rx-a', 'amox-500', 2, source_attempt=1)
    doc['holds'] = [existing]
    _, persisted = run_worker(doc)
    assert persisted['orders'][0]['status'] == 'held'
    assert holds_for(persisted, 'rx-a') == [existing]
    assert persisted['stats']['holds_created'] == 0
    assert persisted['stats']['holds_refreshed'] == 0


def test_stale_same_owner_hold_is_refreshed_in_place():
    doc = base_doc()
    doc['orders'] = [order('rx-a', status='retrying', items=[{'medicine_id': 'ator-10', 'qty': 5}], attempts=1)]
    stale = hold('rx-a', 'ator-10', 2, state='stale', source_attempt=0)
    doc['holds'] = [stale]
    _, persisted = run_worker(doc)
    refreshed = holds_for(persisted, 'rx-a')
    assert len(refreshed) == 1
    assert refreshed[0] == {'order_id': 'rx-a', 'medicine_id': 'ator-10', 'qty': 5, 'owner': 'hold_worker', 'state': 'active', 'source_attempt': 1}
    assert persisted['stats']['holds_refreshed'] == 1


def test_same_owner_holds_for_removed_items_are_cleaned_up():
    doc = base_doc()
    doc['orders'] = [order('rx-a', status='holding', items=[{'medicine_id': 'amox-500', 'qty': 2}])]
    doc['holds'] = [hold('rx-a', 'amox-500', 2), hold('rx-a', 'para-500', 3)]
    _, persisted = run_worker(doc)
    assert [(h['medicine_id'], h['qty']) for h in holds_for(persisted, 'rx-a')] == [('amox-500', 2)]


def test_other_owner_active_hold_reduces_available_stock_and_sets_conflict_retry():
    doc = base_doc()
    doc['inventory']['ator-10']['on_hand'] = 8
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'ator-10', 'qty': 5}], attempts=0, max_attempts=3)]
    protected = hold('rx-other', 'ator-10', 4, owner='manual_counter')
    doc['holds'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'stock_conflict'
    assert protected in persisted['holds']
    assert holds_for(persisted, 'rx-a') == []
    assert persisted['stats']['conflicts'] == 1


def test_exhausted_conflict_fails_without_touching_protected_hold():
    doc = base_doc()
    doc['inventory']['ator-10']['on_hand'] = 8
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'ator-10', 'qty': 5}], attempts=1, max_attempts=2)]
    protected = hold('rx-other', 'ator-10', 4, owner='legacy_import')
    doc['holds'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['orders'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'stock_conflict'
    assert protected in persisted['holds']
    assert persisted['stats']['conflicts'] == 1
    assert persisted['stats']['failures'] == 1


def test_requested_duplicate_item_ids_are_normalized_to_one_hold():
    doc = base_doc()
    doc['orders'] = [order('rx-a', items=[{'medicine_id': 'para-500', 'qty': 2}, {'medicine_id': 'para-500', 'qty': 3}])]
    _, persisted = run_worker(doc)
    rx_holds = holds_for(persisted, 'rx-a')
    assert len(rx_holds) == 1
    assert rx_holds[0]['medicine_id'] == 'para-500'
    assert rx_holds[0]['qty'] == 5

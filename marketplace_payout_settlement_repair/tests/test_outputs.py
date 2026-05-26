import copy
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, '/app')

DATA_PATH = Path('/app/payout_locks.json')


def write_doc(doc):
    DATA_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_doc():
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


def run_worker(doc):
    write_doc(copy.deepcopy(doc))
    import payout_worker
    importlib.reload(payout_worker)
    result = payout_worker.run()
    persisted = read_doc()
    return result, persisted


def base_doc():
    return {
        'balances': {
            'vendor-alpha': {'available_cents': 20},
            'vendor-beta': {'available_cents': 50},
            'vendor-gamma': {'available_cents': 8},
            'vendor-delta': {'available_cents': 3},
            'vendor-rare': {'available_cents': 1},
        },
        'payouts': [],
        'locks': [],
        'stats': {'locks_created': 0, 'locks_refreshed': 0, 'conflicts': 0, 'failures': 0},
    }


def payout(payout_id, status='queued', lines=None, attempts=0, max_attempts=2, metadata=None):
    return {
        'id': payout_id,
        'status': status,
        'lines': lines or [{'vendor_id': 'vendor-alpha', 'amount_cents': 2}],
        'attempts': attempts,
        'max_attempts': max_attempts,
        'metadata': metadata or {},
    }


def lock(payout_id, vendor_id, amount_cents, owner='payout_worker', state='active', source_attempt=0):
    return {
        'payout_id': payout_id,
        'vendor_id': vendor_id,
        'amount_cents': amount_cents,
        'owner': owner,
        'state': state,
        'source_attempt': source_attempt,
    }


def locks_for(doc, payout_id):
    return [h for h in doc['locks'] if h['payout_id'] == payout_id]


def test_worker_return_value_equals_persisted_json():
    doc = base_doc()
    doc['payouts'] = [payout('po-a')]
    result, persisted = run_worker(doc)
    assert result == persisted


def test_happy_path_creates_all_requested_locks_and_marks_settled():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-alpha', 'amount_cents': 2}, {'vendor_id': 'vendor-beta', 'amount_cents': 3}])]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'settled'
    assert updated['attempts'] == 0
    assert sorted((h['vendor_id'], h['amount_cents'], h['owner'], h['state']) for h in locks_for(persisted, 'po-a')) == [
        ('vendor-alpha', 2, 'payout_worker', 'active'),
        ('vendor-beta', 3, 'payout_worker', 'active'),
    ]
    assert persisted['stats']['locks_created'] == 2


def test_missing_balances_retries_without_partial_writes():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-alpha', 'amount_cents': 2}, {'vendor_id': 'vendor-rare', 'amount_cents': 2}], attempts=0, max_attempts=3)]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'funds_unavailable'
    assert locks_for(persisted, 'po-a') == []
    assert persisted['stats']['failures'] == 0


def test_retrying_job_recovers_when_funds_becomes_complete_and_clears_stale_metadata():
    doc = base_doc()
    doc['balances']['vendor-rare']['available_cents'] = 4
    doc['payouts'] = [payout('po-a', status='retrying', lines=[{'vendor_id': 'vendor-rare', 'amount_cents': 2}], attempts=1, max_attempts=3, metadata={'retry_reason': 'funds_unavailable', 'last_error': 'short balance', 'failed_at': 'old', 'channel': 'mobile'})]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'settled'
    assert updated['attempts'] == 1
    assert updated['metadata'] == {'channel': 'mobile'}
    assert locks_for(persisted, 'po-a')[0]['amount_cents'] == 2


def test_exhausted_missing_balances_moves_to_failed_and_counts_failure_once():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-rare', 'amount_cents': 5}], attempts=1, max_attempts=2)]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'funds_unavailable'
    assert persisted['stats']['failures'] == 1
    assert locks_for(persisted, 'po-a') == []


def test_terminal_settled_payout_and_its_lock_are_unchanged():
    doc = base_doc()
    terminal = payout('po-done', status='settled', lines=[{'vendor_id': 'vendor-delta', 'amount_cents': 1}], metadata={'packed_by': 'ali'})
    terminal_lock = lock('po-done', 'vendor-delta', 1, source_attempt=0)
    doc['payouts'] = [terminal]
    doc['locks'] = [terminal_lock]
    _, persisted = run_worker(doc)
    assert persisted['payouts'][0] == terminal
    assert persisted['locks'][0] == terminal_lock
    assert persisted['stats'] == doc['stats']


def test_terminal_failed_and_cancelled_payouts_are_unchanged():
    doc = base_doc()
    failed = payout('po-failed', status='failed', attempts=2, max_attempts=2, metadata={'retry_reason': 'funds_conflict', 'failed_at': '2026-05-01'})
    cancelled = payout('po-cancel', status='cancelled', metadata={'cancelled_by': 'vendor'})
    doc['payouts'] = [failed, cancelled]
    _, persisted = run_worker(doc)
    assert persisted['payouts'] == [failed, cancelled]
    assert persisted['locks'] == []


def test_repeated_runs_do_not_duplicate_locks_or_inflate_counters():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-alpha', 'amount_cents': 2}])]
    first, _ = run_worker(doc)
    second, persisted = run_worker(first)
    assert second == persisted
    assert len(locks_for(persisted, 'po-a')) == 1
    assert persisted['stats']['locks_created'] == 1
    assert persisted['stats']['locks_refreshed'] == 0


def test_existing_matching_partial_lock_is_reused_without_counter_increase():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', status='settling', lines=[{'vendor_id': 'vendor-alpha', 'amount_cents': 2}], attempts=1)]
    existing = lock('po-a', 'vendor-alpha', 2, source_attempt=1)
    doc['locks'] = [existing]
    _, persisted = run_worker(doc)
    assert persisted['payouts'][0]['status'] == 'settled'
    assert locks_for(persisted, 'po-a') == [existing]
    assert persisted['stats']['locks_created'] == 0
    assert persisted['stats']['locks_refreshed'] == 0


def test_stale_same_owner_lock_is_refreshed_in_place():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', status='retrying', lines=[{'vendor_id': 'vendor-gamma', 'amount_cents': 5}], attempts=1)]
    stale = lock('po-a', 'vendor-gamma', 2, state='stale', source_attempt=0)
    doc['locks'] = [stale]
    _, persisted = run_worker(doc)
    refreshed = locks_for(persisted, 'po-a')
    assert len(refreshed) == 1
    assert refreshed[0] == {'payout_id': 'po-a', 'vendor_id': 'vendor-gamma', 'amount_cents': 5, 'owner': 'payout_worker', 'state': 'active', 'source_attempt': 1}
    assert persisted['stats']['locks_refreshed'] == 1


def test_same_owner_locks_for_removed_lines_are_cleaned_up():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', status='settling', lines=[{'vendor_id': 'vendor-alpha', 'amount_cents': 2}])]
    doc['locks'] = [lock('po-a', 'vendor-alpha', 2), lock('po-a', 'vendor-beta', 3)]
    _, persisted = run_worker(doc)
    assert [(h['vendor_id'], h['amount_cents']) for h in locks_for(persisted, 'po-a')] == [('vendor-alpha', 2)]


def test_other_owner_active_lock_reduces_available_funds_and_sets_conflict_retry():
    doc = base_doc()
    doc['balances']['vendor-gamma']['available_cents'] = 8
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-gamma', 'amount_cents': 5}], attempts=0, max_attempts=3)]
    protected = lock('po-other', 'vendor-gamma', 4, owner='finance_override')
    doc['locks'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'funds_conflict'
    assert protected in persisted['locks']
    assert locks_for(persisted, 'po-a') == []
    assert persisted['stats']['conflicts'] == 1


def test_exhausted_conflict_fails_without_touching_protected_lock():
    doc = base_doc()
    doc['balances']['vendor-gamma']['available_cents'] = 8
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-gamma', 'amount_cents': 5}], attempts=1, max_attempts=2)]
    protected = lock('po-other', 'vendor-gamma', 4, owner='legacy_gateway')
    doc['locks'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['payouts'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'funds_conflict'
    assert protected in persisted['locks']
    assert persisted['stats']['conflicts'] == 1
    assert persisted['stats']['failures'] == 1


def test_requested_duplicate_line_ids_are_normalized_to_one_lock():
    doc = base_doc()
    doc['payouts'] = [payout('po-a', lines=[{'vendor_id': 'vendor-beta', 'amount_cents': 2}, {'vendor_id': 'vendor-beta', 'amount_cents': 3}])]
    _, persisted = run_worker(doc)
    rx_locks = locks_for(persisted, 'po-a')
    assert len(rx_locks) == 1
    assert rx_locks[0]['vendor_id'] == 'vendor-beta'
    assert rx_locks[0]['amount_cents'] == 5

import copy
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, '/app')

DATA_PATH = Path('/app/envelope_signers.json')


def write_doc(doc):
    DATA_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_doc():
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


def run_worker(doc):
    write_doc(copy.deepcopy(doc))
    import signer_worker
    importlib.reload(signer_worker)
    result = signer_worker.run()
    persisted = read_doc()
    return result, persisted


def base_doc():
    return {
        'role_capacity': {
            'buyer': {'available_slots': 20},
            'seller': {'available_slots': 50},
            'witness': {'available_slots': 8},
            'notary': {'available_slots': 3},
            'guardian': {'available_slots': 1},
        },
        'envelopes': [],
        'slots': [],
        'stats': {'slots_created': 0, 'slots_refreshed': 0, 'conflicts': 0, 'failures': 0},
    }


def envelope(envelope_id, status='queued', signer_requests=None, attempts=0, max_attempts=2, metadata=None):
    return {
        'id': envelope_id,
        'status': status,
        'signer_requests': signer_requests or [{'signer_role': 'buyer', 'slot_count': 2}],
        'attempts': attempts,
        'max_attempts': max_attempts,
        'metadata': metadata or {},
    }


def slot(envelope_id, signer_role, slot_count, owner='signer_worker', state='active', source_attempt=0):
    return {
        'envelope_id': envelope_id,
        'signer_role': signer_role,
        'slot_count': slot_count,
        'owner': owner,
        'state': state,
        'source_attempt': source_attempt,
    }


def slots_for(doc, envelope_id):
    return [h for h in doc['slots'] if h['envelope_id'] == envelope_id]


def test_worker_return_value_equals_persisted_json():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a')]
    result, persisted = run_worker(doc)
    assert result == persisted


def test_happy_path_creates_all_requested_slots_and_marks_ready():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'buyer', 'slot_count': 2}, {'signer_role': 'seller', 'slot_count': 3}])]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'ready'
    assert updated['attempts'] == 0
    assert sorted((h['signer_role'], h['slot_count'], h['owner'], h['state']) for h in slots_for(persisted, 'env-a')) == [
        ('buyer', 2, 'signer_worker', 'active'),
        ('seller', 3, 'signer_worker', 'active'),
    ]
    assert persisted['stats']['slots_created'] == 2


def test_missing_role_capacity_retries_without_partial_writes():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'buyer', 'slot_count': 2}, {'signer_role': 'guardian', 'slot_count': 2}], attempts=0, max_attempts=3)]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'signer_slot_unavailable'
    assert slots_for(persisted, 'env-a') == []
    assert persisted['stats']['failures'] == 0


def test_retrying_job_recovers_when_signer_slot_becomes_complete_and_clears_stale_metadata():
    doc = base_doc()
    doc['role_capacity']['guardian']['available_slots'] = 4
    doc['envelopes'] = [envelope('env-a', status='retrying', signer_requests=[{'signer_role': 'guardian', 'slot_count': 2}], attempts=1, max_attempts=3, metadata={'retry_reason': 'signer_slot_unavailable', 'last_error': 'missing signer slot', 'failed_at': 'old', 'channel': 'mobile'})]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'ready'
    assert updated['attempts'] == 1
    assert updated['metadata'] == {'channel': 'mobile'}
    assert slots_for(persisted, 'env-a')[0]['slot_count'] == 2


def test_exhausted_missing_role_capacity_moves_to_failed_and_counts_failure_once():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'guardian', 'slot_count': 5}], attempts=1, max_attempts=2)]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'signer_slot_unavailable'
    assert persisted['stats']['failures'] == 1
    assert slots_for(persisted, 'env-a') == []


def test_terminal_ready_envelope_and_its_slot_are_unchanged():
    doc = base_doc()
    terminal = envelope('env-done', status='ready', signer_requests=[{'signer_role': 'notary', 'slot_count': 1}], metadata={'packed_by': 'ali'})
    terminal_slot = slot('env-done', 'notary', 1, source_attempt=0)
    doc['envelopes'] = [terminal]
    doc['slots'] = [terminal_slot]
    _, persisted = run_worker(doc)
    assert persisted['envelopes'][0] == terminal
    assert persisted['slots'][0] == terminal_slot
    assert persisted['stats'] == doc['stats']


def test_terminal_failed_and_cancelled_envelopes_are_unchanged():
    doc = base_doc()
    failed = envelope('env-failed', status='failed', attempts=2, max_attempts=2, metadata={'retry_reason': 'signer_slot_conflict', 'failed_at': '2026-05-01'})
    cancelled = envelope('env-cancel', status='cancelled', metadata={'cancelled_by': 'sender'})
    doc['envelopes'] = [failed, cancelled]
    _, persisted = run_worker(doc)
    assert persisted['envelopes'] == [failed, cancelled]
    assert persisted['slots'] == []


def test_repeated_runs_do_not_duplicate_slots_or_inflate_counters():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'buyer', 'slot_count': 2}])]
    first, _ = run_worker(doc)
    second, persisted = run_worker(first)
    assert second == persisted
    assert len(slots_for(persisted, 'env-a')) == 1
    assert persisted['stats']['slots_created'] == 1
    assert persisted['stats']['slots_refreshed'] == 0


def test_existing_matching_partial_slot_is_reused_without_counter_increase():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', status='preparing', signer_requests=[{'signer_role': 'buyer', 'slot_count': 2}], attempts=1)]
    existing = slot('env-a', 'buyer', 2, source_attempt=1)
    doc['slots'] = [existing]
    _, persisted = run_worker(doc)
    assert persisted['envelopes'][0]['status'] == 'ready'
    assert slots_for(persisted, 'env-a') == [existing]
    assert persisted['stats']['slots_created'] == 0
    assert persisted['stats']['slots_refreshed'] == 0


def test_stale_same_owner_slot_is_refreshed_in_place():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', status='retrying', signer_requests=[{'signer_role': 'witness', 'slot_count': 5}], attempts=1)]
    stale = slot('env-a', 'witness', 2, state='stale', source_attempt=0)
    doc['slots'] = [stale]
    _, persisted = run_worker(doc)
    refreshed = slots_for(persisted, 'env-a')
    assert len(refreshed) == 1
    assert refreshed[0] == {'envelope_id': 'env-a', 'signer_role': 'witness', 'slot_count': 5, 'owner': 'signer_worker', 'state': 'active', 'source_attempt': 1}
    assert persisted['stats']['slots_refreshed'] == 1


def test_same_owner_slots_for_removed_signer_requests_are_cleaned_up():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', status='preparing', signer_requests=[{'signer_role': 'buyer', 'slot_count': 2}])]
    doc['slots'] = [slot('env-a', 'buyer', 2), slot('env-a', 'seller', 3)]
    _, persisted = run_worker(doc)
    assert [(h['signer_role'], h['slot_count']) for h in slots_for(persisted, 'env-a')] == [('buyer', 2)]


def test_other_owner_active_slot_reduces_available_signer_slot_and_sets_conflict_retry():
    doc = base_doc()
    doc['role_capacity']['witness']['available_slots'] = 8
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'witness', 'slot_count': 5}], attempts=0, max_attempts=3)]
    protected = slot('env-other', 'witness', 4, owner='legal_override')
    doc['slots'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'signer_slot_conflict'
    assert protected in persisted['slots']
    assert slots_for(persisted, 'env-a') == []
    assert persisted['stats']['conflicts'] == 1


def test_exhausted_conflict_fails_without_touching_protected_slot():
    doc = base_doc()
    doc['role_capacity']['witness']['available_slots'] = 8
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'witness', 'slot_count': 5}], attempts=1, max_attempts=2)]
    protected = slot('env-other', 'witness', 4, owner='legacy_envelope_import')
    doc['slots'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['envelopes'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'signer_slot_conflict'
    assert protected in persisted['slots']
    assert persisted['stats']['conflicts'] == 1
    assert persisted['stats']['failures'] == 1


def test_requested_duplicate_signer_request_ids_are_normalized_to_one_slot():
    doc = base_doc()
    doc['envelopes'] = [envelope('env-a', signer_requests=[{'signer_role': 'seller', 'slot_count': 2}, {'signer_role': 'seller', 'slot_count': 3}])]
    _, persisted = run_worker(doc)
    rx_slots = slots_for(persisted, 'env-a')
    assert len(rx_slots) == 1
    assert rx_slots[0]['signer_role'] == 'seller'
    assert rx_slots[0]['slot_count'] == 5

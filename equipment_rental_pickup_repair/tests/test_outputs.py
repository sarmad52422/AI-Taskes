import copy
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, '/app')

DATA_PATH = Path('/app/pickup_reservations.json')


def write_doc(doc):
    DATA_PATH.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_doc():
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


def run_worker(doc):
    write_doc(copy.deepcopy(doc))
    import pickup_worker
    importlib.reload(pickup_worker)
    result = pickup_worker.run()
    persisted = read_doc()
    return result, persisted


def base_doc():
    return {
        'inventory': {
            'drill-kit': {'on_hand': 20},
            'ladder-8ft': {'on_hand': 50},
            'tile-saw': {'on_hand': 8},
            'paint-sprayer': {'on_hand': 3},
            'floor-sander': {'on_hand': 1},
        },
        'bookings': [],
        'reservations': [],
        'stats': {'reservations_created': 0, 'reservations_refreshed': 0, 'conflicts': 0, 'failures': 0},
    }


def booking(booking_id, status='queued', items=None, attempts=0, max_attempts=2, metadata=None):
    return {
        'id': booking_id,
        'status': status,
        'items': items or [{'equipment_id': 'drill-kit', 'units': 2}],
        'attempts': attempts,
        'max_attempts': max_attempts,
        'metadata': metadata or {},
    }


def reservation(booking_id, equipment_id, units, owner='pickup_worker', state='active', source_attempt=0):
    return {
        'booking_id': booking_id,
        'equipment_id': equipment_id,
        'units': units,
        'owner': owner,
        'state': state,
        'source_attempt': source_attempt,
    }


def reservations_for(doc, booking_id):
    return [h for h in doc['reservations'] if h['booking_id'] == booking_id]


def test_worker_return_value_equals_persisted_json():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a')]
    result, persisted = run_worker(doc)
    assert result == persisted


def test_happy_path_creates_all_requested_reservations_and_marks_allocated():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'drill-kit', 'units': 2}, {'equipment_id': 'ladder-8ft', 'units': 3}])]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'allocated'
    assert updated['attempts'] == 0
    assert sorted((h['equipment_id'], h['units'], h['owner'], h['state']) for h in reservations_for(persisted, 'rent-a')) == [
        ('drill-kit', 2, 'pickup_worker', 'active'),
        ('ladder-8ft', 3, 'pickup_worker', 'active'),
    ]
    assert persisted['stats']['reservations_created'] == 2


def test_missing_inventory_retries_without_partial_writes():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'drill-kit', 'units': 2}, {'equipment_id': 'floor-sander', 'units': 2}], attempts=0, max_attempts=3)]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'unit_unavailable'
    assert reservations_for(persisted, 'rent-a') == []
    assert persisted['stats']['failures'] == 0


def test_retrying_job_recovers_when_unit_becomes_complete_and_clears_stale_metadata():
    doc = base_doc()
    doc['inventory']['floor-sander']['on_hand'] = 4
    doc['bookings'] = [booking('rent-a', status='retrying', items=[{'equipment_id': 'floor-sander', 'units': 2}], attempts=1, max_attempts=3, metadata={'retry_reason': 'unit_unavailable', 'last_error': 'short pick', 'failed_at': 'old', 'channel': 'mobile'})]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'allocated'
    assert updated['attempts'] == 1
    assert updated['metadata'] == {'channel': 'mobile'}
    assert reservations_for(persisted, 'rent-a')[0]['units'] == 2


def test_exhausted_missing_inventory_moves_to_failed_and_counts_failure_once():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'floor-sander', 'units': 5}], attempts=1, max_attempts=2)]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'unit_unavailable'
    assert persisted['stats']['failures'] == 1
    assert reservations_for(persisted, 'rent-a') == []


def test_terminal_allocated_booking_and_its_reservation_are_unchanged():
    doc = base_doc()
    terminal = booking('rent-done', status='allocated', items=[{'equipment_id': 'paint-sprayer', 'units': 1}], metadata={'packed_by': 'ali'})
    terminal_reservation = reservation('rent-done', 'paint-sprayer', 1, source_attempt=0)
    doc['bookings'] = [terminal]
    doc['reservations'] = [terminal_reservation]
    _, persisted = run_worker(doc)
    assert persisted['bookings'][0] == terminal
    assert persisted['reservations'][0] == terminal_reservation
    assert persisted['stats'] == doc['stats']


def test_terminal_failed_and_cancelled_bookings_are_unchanged():
    doc = base_doc()
    failed = booking('rent-failed', status='failed', attempts=2, max_attempts=2, metadata={'retry_reason': 'unit_conflict', 'failed_at': '2026-05-01'})
    cancelled = booking('rent-cancel', status='cancelled', metadata={'cancelled_by': 'customer'})
    doc['bookings'] = [failed, cancelled]
    _, persisted = run_worker(doc)
    assert persisted['bookings'] == [failed, cancelled]
    assert persisted['reservations'] == []


def test_repeated_runs_do_not_duplicate_reservations_or_inflate_counters():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'drill-kit', 'units': 2}])]
    first, _ = run_worker(doc)
    second, persisted = run_worker(first)
    assert second == persisted
    assert len(reservations_for(persisted, 'rent-a')) == 1
    assert persisted['stats']['reservations_created'] == 1
    assert persisted['stats']['reservations_refreshed'] == 0


def test_existing_matching_partial_reservation_is_reused_without_counter_increase():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', status='allocating', items=[{'equipment_id': 'drill-kit', 'units': 2}], attempts=1)]
    existing = reservation('rent-a', 'drill-kit', 2, source_attempt=1)
    doc['reservations'] = [existing]
    _, persisted = run_worker(doc)
    assert persisted['bookings'][0]['status'] == 'allocated'
    assert reservations_for(persisted, 'rent-a') == [existing]
    assert persisted['stats']['reservations_created'] == 0
    assert persisted['stats']['reservations_refreshed'] == 0


def test_stale_same_owner_reservation_is_refreshed_in_place():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', status='retrying', items=[{'equipment_id': 'tile-saw', 'units': 5}], attempts=1)]
    stale = reservation('rent-a', 'tile-saw', 2, state='stale', source_attempt=0)
    doc['reservations'] = [stale]
    _, persisted = run_worker(doc)
    refreshed = reservations_for(persisted, 'rent-a')
    assert len(refreshed) == 1
    assert refreshed[0] == {'booking_id': 'rent-a', 'equipment_id': 'tile-saw', 'units': 5, 'owner': 'pickup_worker', 'state': 'active', 'source_attempt': 1}
    assert persisted['stats']['reservations_refreshed'] == 1


def test_same_owner_reservations_for_removed_items_are_cleaned_up():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', status='allocating', items=[{'equipment_id': 'drill-kit', 'units': 2}])]
    doc['reservations'] = [reservation('rent-a', 'drill-kit', 2), reservation('rent-a', 'ladder-8ft', 3)]
    _, persisted = run_worker(doc)
    assert [(h['equipment_id'], h['units']) for h in reservations_for(persisted, 'rent-a')] == [('drill-kit', 2)]


def test_other_owner_active_reservation_reduces_available_unit_and_sets_conflict_retry():
    doc = base_doc()
    doc['inventory']['tile-saw']['on_hand'] = 8
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'tile-saw', 'units': 5}], attempts=0, max_attempts=3)]
    protected = reservation('rent-other', 'tile-saw', 4, owner='counter_agent')
    doc['reservations'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'retrying'
    assert updated['attempts'] == 1
    assert updated['metadata']['retry_reason'] == 'unit_conflict'
    assert protected in persisted['reservations']
    assert reservations_for(persisted, 'rent-a') == []
    assert persisted['stats']['conflicts'] == 1


def test_exhausted_conflict_fails_without_touching_protected_reservation():
    doc = base_doc()
    doc['inventory']['tile-saw']['on_hand'] = 8
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'tile-saw', 'units': 5}], attempts=1, max_attempts=2)]
    protected = reservation('rent-other', 'tile-saw', 4, owner='fleet_import')
    doc['reservations'] = [protected]
    _, persisted = run_worker(doc)
    updated = persisted['bookings'][0]
    assert updated['status'] == 'failed'
    assert updated['attempts'] == 2
    assert updated['metadata']['retry_reason'] == 'unit_conflict'
    assert protected in persisted['reservations']
    assert persisted['stats']['conflicts'] == 1
    assert persisted['stats']['failures'] == 1


def test_requested_duplicate_item_ids_are_normalized_to_one_reservation():
    doc = base_doc()
    doc['bookings'] = [booking('rent-a', items=[{'equipment_id': 'ladder-8ft', 'units': 2}, {'equipment_id': 'ladder-8ft', 'units': 3}])]
    _, persisted = run_worker(doc)
    rx_reservations = reservations_for(persisted, 'rent-a')
    assert len(rx_reservations) == 1
    assert rx_reservations[0]['equipment_id'] == 'ladder-8ft'
    assert rx_reservations[0]['units'] == 5

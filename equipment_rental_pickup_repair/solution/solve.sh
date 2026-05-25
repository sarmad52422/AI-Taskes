#!/bin/bash
set -e

cat > /app/pickup_store.py <<'PY'
import json
from pathlib import Path

DATA_PATH = Path('/app/pickup_reservations.json')


def load_document(path=DATA_PATH):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return document
PY

cat > /app/pickup_rules.py <<'PY'
ACTIVE_STATUSES = {'queued', 'allocating', 'retrying'}
TERMINAL_STATUSES = {'allocated', 'failed', 'cancelled'}
WORKER_OWNER = 'pickup_worker'
STALE_KEYS = {'retry_reason', 'last_error', 'failed_at'}


def is_active(booking):
    return booking.get('status') in ACTIVE_STATUSES


def requested_units(booking):
    requested = {}
    for item in booking.get('items', []):
        equipment_id = item.get('equipment_id')
        if not equipment_id:
            continue
        requested[equipment_id] = requested.get(equipment_id, 0) + int(item.get('units', 0))
    return requested


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def active_external_allocated(reservations, booking_id, equipment_id):
    total = 0
    for reservation in reservations:
        if reservation.get('state') != 'active':
            continue
        if reservation.get('equipment_id') != equipment_id:
            continue
        if reservation.get('owner') == WORKER_OWNER and reservation.get('booking_id') == booking_id:
            continue
        total += int(reservation.get('units', 0))
    return total


def has_external_pressure(reservations, booking_id, equipment_id):
    for reservation in reservations:
        if reservation.get('state') != 'active':
            continue
        if reservation.get('equipment_id') != equipment_id:
            continue
        if reservation.get('owner') != WORKER_OWNER or reservation.get('booking_id') != booking_id:
            return True
    return False


def available_for_booking(document, booking_id, equipment_id):
    unit = int(document.get('inventory', {}).get(equipment_id, {}).get('on_hand', 0))
    return unit - active_external_allocated(document.get('reservations', []), booking_id, equipment_id)
PY

cat > /app/pickup_worker.py <<'PY'
from pickup_store import load_document, save_document
from pickup_rules import (
    WORKER_OWNER,
    available_for_booking,
    clear_stale_metadata,
    has_external_pressure,
    is_active,
    requested_units,
)


def _stats(document):
    stats = document.setdefault('stats', {})
    for key in ('reservations_created', 'reservations_refreshed', 'conflicts', 'failures'):
        stats.setdefault(key, 0)
    return stats


def _same_owner_reservations(document, booking_id):
    return [
        reservation for reservation in document.get('reservations', [])
        if reservation.get('booking_id') == booking_id and reservation.get('owner') == WORKER_OWNER
    ]


def _active_requested_reservation(document, booking_id, equipment_id, units):
    for reservation in _same_owner_reservations(document, booking_id):
        if reservation.get('equipment_id') == equipment_id and reservation.get('state') == 'active' and int(reservation.get('units', 0)) == units:
            return reservation
    return None


def _refreshable_reservation(document, booking_id, equipment_id):
    for reservation in _same_owner_reservations(document, booking_id):
        if reservation.get('equipment_id') == equipment_id:
            return reservation
    return None


def _remove_unrequested_worker_reservations(document, booking_id, requested_ids):
    document['reservations'] = [
        reservation for reservation in document.get('reservations', [])
        if not (
            reservation.get('booking_id') == booking_id
            and reservation.get('owner') == WORKER_OWNER
            and reservation.get('equipment_id') not in requested_ids
        )
    ]


def _can_reserve_all(document, booking, requested):
    for equipment_id, units in requested.items():
        if available_for_booking(document, booking['id'], equipment_id) < units:
            reason = 'unit_conflict' if has_external_pressure(document.get('reservations', []), booking['id'], equipment_id) else 'unit_unavailable'
            return False, reason
    return True, None


def _mark_unavailable(document, booking, reason):
    stats = _stats(document)
    booking['attempts'] = int(booking.get('attempts', 0)) + 1
    booking.setdefault('metadata', {})['retry_reason'] = reason
    if reason == 'unit_conflict':
        stats['conflicts'] += 1
    if booking['attempts'] >= int(booking.get('max_attempts', 1)):
        booking['status'] = 'failed'
        stats['failures'] += 1
    else:
        booking['status'] = 'retrying'


def _apply_reservations(document, booking, requested):
    stats = _stats(document)
    _remove_unrequested_worker_reservations(document, booking['id'], set(requested))
    for equipment_id, units in requested.items():
        existing = _active_requested_reservation(document, booking['id'], equipment_id, units)
        if existing is not None:
            continue
        refreshable = _refreshable_reservation(document, booking['id'], equipment_id)
        if refreshable is not None:
            refreshable['units'] = units
            refreshable['state'] = 'active'
            refreshable['owner'] = WORKER_OWNER
            refreshable['source_attempt'] = int(booking.get('attempts', 0))
            stats['reservations_refreshed'] += 1
            continue
        document.setdefault('reservations', []).append({
            'booking_id': booking['id'],
            'equipment_id': equipment_id,
            'units': units,
            'owner': WORKER_OWNER,
            'state': 'active',
            'source_attempt': int(booking.get('attempts', 0)),
        })
        stats['reservations_created'] += 1


def _process_booking(document, booking):
    requested = requested_units(booking)
    can_reserve, reason = _can_reserve_all(document, booking, requested)
    if not can_reserve:
        _mark_unavailable(document, booking, reason)
        return
    _apply_reservations(document, booking, requested)
    booking['status'] = 'allocated'
    booking['metadata'] = clear_stale_metadata(booking.get('metadata', {}))


def run():
    document = load_document()
    _stats(document)
    for booking in document.get('bookings', []):
        if is_active(booking):
            _process_booking(document, booking)
    return save_document(document)


if __name__ == '__main__':
    run()
PY

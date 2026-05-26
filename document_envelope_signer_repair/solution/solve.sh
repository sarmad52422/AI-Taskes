#!/bin/bash
set -e

cat > /app/signer_store.py <<'PY'
import json
from pathlib import Path

DATA_PATH = Path('/app/envelope_signers.json')


def load_document(path=DATA_PATH):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return document
PY

cat > /app/signer_rules.py <<'PY'
ACTIVE_STATUSES = {'queued', 'preparing', 'retrying'}
TERMINAL_STATUSES = {'ready', 'failed', 'cancelled'}
WORKER_OWNER = 'signer_worker'
STALE_KEYS = {'retry_reason', 'last_error', 'failed_at'}


def is_active(envelope):
    return envelope.get('status') in ACTIVE_STATUSES


def requested_quantities(envelope):
    requested = {}
    for signer_request in envelope.get('signer_requests', []):
        signer_role = signer_request.get('signer_role')
        if not signer_role:
            continue
        requested[signer_role] = requested.get(signer_role, 0) + int(signer_request.get('slot_count', 0))
    return requested


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def active_external_reserved(slots, envelope_id, signer_role):
    total = 0
    for slot in slots:
        if slot.get('state') != 'active':
            continue
        if slot.get('signer_role') != signer_role:
            continue
        if slot.get('owner') == WORKER_OWNER and slot.get('envelope_id') == envelope_id:
            continue
        total += int(slot.get('slot_count', 0))
    return total


def has_external_pressure(slots, envelope_id, signer_role):
    for slot in slots:
        if slot.get('state') != 'active':
            continue
        if slot.get('signer_role') != signer_role:
            continue
        if slot.get('owner') != WORKER_OWNER or slot.get('envelope_id') != envelope_id:
            return True
    return False


def available_for_envelope(document, envelope_id, signer_role):
    capacity = int(document.get('role_capacity', {}).get(signer_role, {}).get('available_slots', 0))
    return capacity - active_external_reserved(document.get('slots', []), envelope_id, signer_role)
PY

cat > /app/signer_worker.py <<'PY'
from signer_store import load_document, save_document
from signer_rules import (
    WORKER_OWNER,
    available_for_envelope,
    clear_stale_metadata,
    has_external_pressure,
    is_active,
    requested_quantities,
)


def _stats(document):
    stats = document.setdefault('stats', {})
    for key in ('slots_created', 'slots_refreshed', 'conflicts', 'failures'):
        stats.setdefault(key, 0)
    return stats


def _same_owner_slots(document, envelope_id):
    return [
        slot for slot in document.get('slots', [])
        if slot.get('envelope_id') == envelope_id and slot.get('owner') == WORKER_OWNER
    ]


def _active_requested_slot(document, envelope_id, signer_role, slot_count):
    for slot in _same_owner_slots(document, envelope_id):
        if slot.get('signer_role') == signer_role and slot.get('state') == 'active' and int(slot.get('slot_count', 0)) == slot_count:
            return slot
    return None


def _refreshable_slot(document, envelope_id, signer_role):
    for slot in _same_owner_slots(document, envelope_id):
        if slot.get('signer_role') == signer_role:
            return slot
    return None


def _remove_unrequested_worker_slots(document, envelope_id, requested_ids):
    document['slots'] = [
        slot for slot in document.get('slots', [])
        if not (
            slot.get('envelope_id') == envelope_id
            and slot.get('owner') == WORKER_OWNER
            and slot.get('signer_role') not in requested_ids
        )
    ]


def _can_reserve_all(document, envelope, requested):
    for signer_role, slot_count in requested.items():
        if available_for_envelope(document, envelope['id'], signer_role) < slot_count:
            reason = 'signer_slot_conflict' if has_external_pressure(document.get('slots', []), envelope['id'], signer_role) else 'signer_slot_unavailable'
            return False, reason
    return True, None


def _mark_unavailable(document, envelope, reason):
    stats = _stats(document)
    envelope['attempts'] = int(envelope.get('attempts', 0)) + 1
    envelope.setdefault('metadata', {})['retry_reason'] = reason
    if reason == 'signer_slot_conflict':
        stats['conflicts'] += 1
    if envelope['attempts'] >= int(envelope.get('max_attempts', 1)):
        envelope['status'] = 'failed'
        stats['failures'] += 1
    else:
        envelope['status'] = 'retrying'


def _apply_slots(document, envelope, requested):
    stats = _stats(document)
    _remove_unrequested_worker_slots(document, envelope['id'], set(requested))
    for signer_role, slot_count in requested.items():
        existing = _active_requested_slot(document, envelope['id'], signer_role, slot_count)
        if existing is not None:
            continue
        refreshable = _refreshable_slot(document, envelope['id'], signer_role)
        if refreshable is not None:
            refreshable['slot_count'] = slot_count
            refreshable['state'] = 'active'
            refreshable['owner'] = WORKER_OWNER
            refreshable['source_attempt'] = int(envelope.get('attempts', 0))
            stats['slots_refreshed'] += 1
            continue
        document.setdefault('slots', []).append({
            'envelope_id': envelope['id'],
            'signer_role': signer_role,
            'slot_count': slot_count,
            'owner': WORKER_OWNER,
            'state': 'active',
            'source_attempt': int(envelope.get('attempts', 0)),
        })
        stats['slots_created'] += 1


def _process_envelope(document, envelope):
    requested = requested_quantities(envelope)
    can_reserve, reason = _can_reserve_all(document, envelope, requested)
    if not can_reserve:
        _mark_unavailable(document, envelope, reason)
        return
    _apply_slots(document, envelope, requested)
    envelope['status'] = 'ready'
    envelope['metadata'] = clear_stale_metadata(envelope.get('metadata', {}))


def run():
    document = load_document()
    _stats(document)
    for envelope in document.get('envelopes', []):
        if is_active(envelope):
            _process_envelope(document, envelope)
    return save_document(document)


if __name__ == '__main__':
    run()
PY

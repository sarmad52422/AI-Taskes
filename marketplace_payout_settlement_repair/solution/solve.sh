#!/bin/bash
set -e

cat > /app/payout_store.py <<'PY'
import json
from pathlib import Path

DATA_PATH = Path('/app/payout_locks.json')


def load_document(path=DATA_PATH):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return document
PY

cat > /app/payout_rules.py <<'PY'
ACTIVE_STATUSES = {'queued', 'settling', 'retrying'}
TERMINAL_STATUSES = {'settled', 'failed', 'cancelled'}
WORKER_OWNER = 'payout_worker'
STALE_KEYS = {'retry_reason', 'last_error', 'failed_at'}


def is_active(payout):
    return payout.get('status') in ACTIVE_STATUSES


def requested_quantities(payout):
    requested = {}
    for line in payout.get('lines', []):
        vendor_id = line.get('vendor_id')
        if not vendor_id:
            continue
        requested[vendor_id] = requested.get(vendor_id, 0) + int(line.get('amount_cents', 0))
    return requested


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def active_external_reserved(locks, payout_id, vendor_id):
    total = 0
    for lock in locks:
        if lock.get('state') != 'active':
            continue
        if lock.get('vendor_id') != vendor_id:
            continue
        if lock.get('owner') == WORKER_OWNER and lock.get('payout_id') == payout_id:
            continue
        total += int(lock.get('amount_cents', 0))
    return total


def has_external_pressure(locks, payout_id, vendor_id):
    for lock in locks:
        if lock.get('state') != 'active':
            continue
        if lock.get('vendor_id') != vendor_id:
            continue
        if lock.get('owner') != WORKER_OWNER or lock.get('payout_id') != payout_id:
            return True
    return False


def available_for_payout(document, payout_id, vendor_id):
    funds = int(document.get('balances', {}).get(vendor_id, {}).get('available_cents', 0))
    return funds - active_external_reserved(document.get('locks', []), payout_id, vendor_id)
PY

cat > /app/payout_worker.py <<'PY'
from payout_store import load_document, save_document
from payout_rules import (
    WORKER_OWNER,
    available_for_payout,
    clear_stale_metadata,
    has_external_pressure,
    is_active,
    requested_quantities,
)


def _stats(document):
    stats = document.setdefault('stats', {})
    for key in ('locks_created', 'locks_refreshed', 'conflicts', 'failures'):
        stats.setdefault(key, 0)
    return stats


def _same_owner_locks(document, payout_id):
    return [
        lock for lock in document.get('locks', [])
        if lock.get('payout_id') == payout_id and lock.get('owner') == WORKER_OWNER
    ]


def _active_requested_lock(document, payout_id, vendor_id, amount_cents):
    for lock in _same_owner_locks(document, payout_id):
        if lock.get('vendor_id') == vendor_id and lock.get('state') == 'active' and int(lock.get('amount_cents', 0)) == amount_cents:
            return lock
    return None


def _refreshable_lock(document, payout_id, vendor_id):
    for lock in _same_owner_locks(document, payout_id):
        if lock.get('vendor_id') == vendor_id:
            return lock
    return None


def _remove_unrequested_worker_locks(document, payout_id, requested_ids):
    document['locks'] = [
        lock for lock in document.get('locks', [])
        if not (
            lock.get('payout_id') == payout_id
            and lock.get('owner') == WORKER_OWNER
            and lock.get('vendor_id') not in requested_ids
        )
    ]


def _can_reserve_all(document, payout, requested):
    for vendor_id, amount_cents in requested.items():
        if available_for_payout(document, payout['id'], vendor_id) < amount_cents:
            reason = 'funds_conflict' if has_external_pressure(document.get('locks', []), payout['id'], vendor_id) else 'funds_unavailable'
            return False, reason
    return True, None


def _mark_unavailable(document, payout, reason):
    stats = _stats(document)
    payout['attempts'] = int(payout.get('attempts', 0)) + 1
    payout.setdefault('metadata', {})['retry_reason'] = reason
    if reason == 'funds_conflict':
        stats['conflicts'] += 1
    if payout['attempts'] >= int(payout.get('max_attempts', 1)):
        payout['status'] = 'failed'
        stats['failures'] += 1
    else:
        payout['status'] = 'retrying'


def _apply_locks(document, payout, requested):
    stats = _stats(document)
    _remove_unrequested_worker_locks(document, payout['id'], set(requested))
    for vendor_id, amount_cents in requested.items():
        existing = _active_requested_lock(document, payout['id'], vendor_id, amount_cents)
        if existing is not None:
            continue
        refreshable = _refreshable_lock(document, payout['id'], vendor_id)
        if refreshable is not None:
            refreshable['amount_cents'] = amount_cents
            refreshable['state'] = 'active'
            refreshable['owner'] = WORKER_OWNER
            refreshable['source_attempt'] = int(payout.get('attempts', 0))
            stats['locks_refreshed'] += 1
            continue
        document.setdefault('locks', []).append({
            'payout_id': payout['id'],
            'vendor_id': vendor_id,
            'amount_cents': amount_cents,
            'owner': WORKER_OWNER,
            'state': 'active',
            'source_attempt': int(payout.get('attempts', 0)),
        })
        stats['locks_created'] += 1


def _process_payout(document, payout):
    requested = requested_quantities(payout)
    can_reserve, reason = _can_reserve_all(document, payout, requested)
    if not can_reserve:
        _mark_unavailable(document, payout, reason)
        return
    _apply_locks(document, payout, requested)
    payout['status'] = 'settled'
    payout['metadata'] = clear_stale_metadata(payout.get('metadata', {}))


def run():
    document = load_document()
    _stats(document)
    for payout in document.get('payouts', []):
        if is_active(payout):
            _process_payout(document, payout)
    return save_document(document)


if __name__ == '__main__':
    run()
PY

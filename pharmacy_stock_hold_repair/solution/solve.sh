#!/bin/bash
set -e

cat > /app/hold_store.py <<'PY'
import json
from pathlib import Path

DATA_PATH = Path('/app/stock_holds.json')


def load_document(path=DATA_PATH):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return document
PY

cat > /app/hold_rules.py <<'PY'
ACTIVE_STATUSES = {'queued', 'holding', 'retrying'}
TERMINAL_STATUSES = {'held', 'failed', 'cancelled'}
WORKER_OWNER = 'hold_worker'
STALE_KEYS = {'retry_reason', 'last_error', 'failed_at'}


def is_active(order):
    return order.get('status') in ACTIVE_STATUSES


def requested_quantities(order):
    requested = {}
    for item in order.get('items', []):
        medicine_id = item.get('medicine_id')
        if not medicine_id:
            continue
        requested[medicine_id] = requested.get(medicine_id, 0) + int(item.get('qty', 0))
    return requested


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def active_external_reserved(holds, order_id, medicine_id):
    total = 0
    for hold in holds:
        if hold.get('state') != 'active':
            continue
        if hold.get('medicine_id') != medicine_id:
            continue
        if hold.get('owner') == WORKER_OWNER and hold.get('order_id') == order_id:
            continue
        total += int(hold.get('qty', 0))
    return total


def has_external_pressure(holds, order_id, medicine_id):
    for hold in holds:
        if hold.get('state') != 'active':
            continue
        if hold.get('medicine_id') != medicine_id:
            continue
        if hold.get('owner') != WORKER_OWNER or hold.get('order_id') != order_id:
            return True
    return False


def available_for_order(document, order_id, medicine_id):
    stock = int(document.get('inventory', {}).get(medicine_id, {}).get('on_hand', 0))
    return stock - active_external_reserved(document.get('holds', []), order_id, medicine_id)
PY

cat > /app/hold_worker.py <<'PY'
from hold_store import load_document, save_document
from hold_rules import (
    WORKER_OWNER,
    available_for_order,
    clear_stale_metadata,
    has_external_pressure,
    is_active,
    requested_quantities,
)


def _stats(document):
    stats = document.setdefault('stats', {})
    for key in ('holds_created', 'holds_refreshed', 'conflicts', 'failures'):
        stats.setdefault(key, 0)
    return stats


def _same_owner_holds(document, order_id):
    return [
        hold for hold in document.get('holds', [])
        if hold.get('order_id') == order_id and hold.get('owner') == WORKER_OWNER
    ]


def _active_requested_hold(document, order_id, medicine_id, qty):
    for hold in _same_owner_holds(document, order_id):
        if hold.get('medicine_id') == medicine_id and hold.get('state') == 'active' and int(hold.get('qty', 0)) == qty:
            return hold
    return None


def _refreshable_hold(document, order_id, medicine_id):
    for hold in _same_owner_holds(document, order_id):
        if hold.get('medicine_id') == medicine_id:
            return hold
    return None


def _remove_unrequested_worker_holds(document, order_id, requested_ids):
    document['holds'] = [
        hold for hold in document.get('holds', [])
        if not (
            hold.get('order_id') == order_id
            and hold.get('owner') == WORKER_OWNER
            and hold.get('medicine_id') not in requested_ids
        )
    ]


def _can_reserve_all(document, order, requested):
    for medicine_id, qty in requested.items():
        if available_for_order(document, order['id'], medicine_id) < qty:
            reason = 'stock_conflict' if has_external_pressure(document.get('holds', []), order['id'], medicine_id) else 'stock_unavailable'
            return False, reason
    return True, None


def _mark_unavailable(document, order, reason):
    stats = _stats(document)
    order['attempts'] = int(order.get('attempts', 0)) + 1
    order.setdefault('metadata', {})['retry_reason'] = reason
    if reason == 'stock_conflict':
        stats['conflicts'] += 1
    if order['attempts'] >= int(order.get('max_attempts', 1)):
        order['status'] = 'failed'
        stats['failures'] += 1
    else:
        order['status'] = 'retrying'


def _apply_holds(document, order, requested):
    stats = _stats(document)
    _remove_unrequested_worker_holds(document, order['id'], set(requested))
    for medicine_id, qty in requested.items():
        existing = _active_requested_hold(document, order['id'], medicine_id, qty)
        if existing is not None:
            continue
        refreshable = _refreshable_hold(document, order['id'], medicine_id)
        if refreshable is not None:
            refreshable['qty'] = qty
            refreshable['state'] = 'active'
            refreshable['owner'] = WORKER_OWNER
            refreshable['source_attempt'] = int(order.get('attempts', 0))
            stats['holds_refreshed'] += 1
            continue
        document.setdefault('holds', []).append({
            'order_id': order['id'],
            'medicine_id': medicine_id,
            'qty': qty,
            'owner': WORKER_OWNER,
            'state': 'active',
            'source_attempt': int(order.get('attempts', 0)),
        })
        stats['holds_created'] += 1


def _process_order(document, order):
    requested = requested_quantities(order)
    can_reserve, reason = _can_reserve_all(document, order, requested)
    if not can_reserve:
        _mark_unavailable(document, order, reason)
        return
    _apply_holds(document, order, requested)
    order['status'] = 'held'
    order['metadata'] = clear_stale_metadata(order.get('metadata', {}))


def run():
    document = load_document()
    _stats(document)
    for order in document.get('orders', []):
        if is_active(order):
            _process_order(document, order)
    return save_document(document)


if __name__ == '__main__':
    run()
PY

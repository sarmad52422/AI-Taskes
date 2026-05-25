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
        requested[medicine_id] = requested.get(medicine_id, 0) + int(item.get('qty', 0))
    return requested


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


def available_for_order(document, order_id, medicine_id):
    stock = document.get('inventory', {}).get(medicine_id, {}).get('on_hand', 0)
    return int(stock) - active_external_reserved(document.get('holds', []), order_id, medicine_id)


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned

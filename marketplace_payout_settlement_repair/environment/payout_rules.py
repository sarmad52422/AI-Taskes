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
        requested[vendor_id] = requested.get(vendor_id, 0) + int(line.get('amount_cents', 0))
    return requested


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


def available_for_payout(document, payout_id, vendor_id):
    funds = document.get('balances', {}).get(vendor_id, {}).get('available_cents', 0)
    return int(funds) - active_external_reserved(document.get('locks', []), payout_id, vendor_id)


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned

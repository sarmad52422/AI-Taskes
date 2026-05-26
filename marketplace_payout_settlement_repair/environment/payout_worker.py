from payout_store import load_document, save_document
from payout_rules import WORKER_OWNER, is_active, requested_quantities, available_for_payout


def _find_lock(document, payout_id, vendor_id):
    for lock in document.get('locks', []):
        if lock.get('payout_id') == payout_id and lock.get('vendor_id') == vendor_id:
            return lock
    return None


def _reserve_line(document, payout, vendor_id, amount_cents):
    lock = _find_lock(document, payout['id'], vendor_id)
    if lock:
        lock['amount_cents'] = amount_cents
        lock['state'] = 'active'
        lock['owner'] = WORKER_OWNER
        return 'refreshed'
    document.setdefault('locks', []).append({
        'payout_id': payout['id'],
        'vendor_id': vendor_id,
        'amount_cents': amount_cents,
        'owner': WORKER_OWNER,
        'state': 'active',
        'source_attempt': payout.get('attempts', 0),
    })
    return 'created'


def _process_payout(document, payout):
    requested = requested_quantities(payout)
    for vendor_id, amount_cents in requested.items():
        if available_for_payout(document, payout['id'], vendor_id) < amount_cents:
            payout['attempts'] = payout.get('attempts', 0) + 1
            payout['status'] = 'failed' if payout['attempts'] >= payout.get('max_attempts', 1) else 'retrying'
            payout.setdefault('metadata', {})['retry_reason'] = 'funds_unavailable'
            document.setdefault('stats', {})['failures'] = document.setdefault('stats', {}).get('failures', 0) + 1
            return
        action = _reserve_line(document, payout, vendor_id, amount_cents)
        key = 'locks_created' if action == 'created' else 'locks_refreshed'
        document.setdefault('stats', {})[key] = document.setdefault('stats', {}).get(key, 0) + 1
    payout['status'] = 'settled'


def run():
    document = load_document()
    for payout in document.get('payouts', []):
        if is_active(payout) or payout.get('status') == 'settled':
            _process_payout(document, payout)
    return save_document(document)


if __name__ == '__main__':
    run()

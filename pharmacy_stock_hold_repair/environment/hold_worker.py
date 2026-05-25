from hold_store import load_document, save_document
from hold_rules import WORKER_OWNER, is_active, requested_quantities, available_for_order


def _find_hold(document, order_id, medicine_id):
    for hold in document.get('holds', []):
        if hold.get('order_id') == order_id and hold.get('medicine_id') == medicine_id:
            return hold
    return None


def _reserve_item(document, order, medicine_id, qty):
    hold = _find_hold(document, order['id'], medicine_id)
    if hold:
        hold['qty'] = qty
        hold['state'] = 'active'
        hold['owner'] = WORKER_OWNER
        return 'refreshed'
    document.setdefault('holds', []).append({
        'order_id': order['id'],
        'medicine_id': medicine_id,
        'qty': qty,
        'owner': WORKER_OWNER,
        'state': 'active',
        'source_attempt': order.get('attempts', 0),
    })
    return 'created'


def _process_order(document, order):
    requested = requested_quantities(order)
    for medicine_id, qty in requested.items():
        if available_for_order(document, order['id'], medicine_id) < qty:
            order['attempts'] = order.get('attempts', 0) + 1
            order['status'] = 'failed' if order['attempts'] >= order.get('max_attempts', 1) else 'retrying'
            order.setdefault('metadata', {})['retry_reason'] = 'stock_unavailable'
            document.setdefault('stats', {})['failures'] = document.setdefault('stats', {}).get('failures', 0) + 1
            return
        action = _reserve_item(document, order, medicine_id, qty)
        key = 'holds_created' if action == 'created' else 'holds_refreshed'
        document.setdefault('stats', {})[key] = document.setdefault('stats', {}).get(key, 0) + 1
    order['status'] = 'held'


def run():
    document = load_document()
    for order in document.get('orders', []):
        if is_active(order) or order.get('status') == 'held':
            _process_order(document, order)
    return save_document(document)


if __name__ == '__main__':
    run()

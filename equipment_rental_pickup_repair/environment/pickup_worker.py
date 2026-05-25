from pickup_store import load_document, save_document
from pickup_rules import WORKER_OWNER, is_active, requested_units, available_for_booking


def _find_reservation(document, booking_id, equipment_id):
    for reservation in document.get('reservations', []):
        if reservation.get('booking_id') == booking_id and reservation.get('equipment_id') == equipment_id:
            return reservation
    return None


def _reserve_item(document, booking, equipment_id, units):
    reservation = _find_reservation(document, booking['id'], equipment_id)
    if reservation:
        reservation['units'] = units
        reservation['state'] = 'active'
        reservation['owner'] = WORKER_OWNER
        return 'refreshed'
    document.setdefault('reservations', []).append({
        'booking_id': booking['id'],
        'equipment_id': equipment_id,
        'units': units,
        'owner': WORKER_OWNER,
        'state': 'active',
        'source_attempt': booking.get('attempts', 0),
    })
    return 'created'


def _process_booking(document, booking):
    requested = requested_units(booking)
    for equipment_id, units in requested.items():
        if available_for_booking(document, booking['id'], equipment_id) < units:
            booking['attempts'] = booking.get('attempts', 0) + 1
            booking['status'] = 'failed' if booking['attempts'] >= booking.get('max_attempts', 1) else 'retrying'
            booking.setdefault('metadata', {})['retry_reason'] = 'unit_unavailable'
            document.setdefault('stats', {})['failures'] = document.setdefault('stats', {}).get('failures', 0) + 1
            return
        action = _reserve_item(document, booking, equipment_id, units)
        key = 'reservations_created' if action == 'created' else 'reservations_refreshed'
        document.setdefault('stats', {})[key] = document.setdefault('stats', {}).get(key, 0) + 1
    booking['status'] = 'allocated'


def run():
    document = load_document()
    for booking in document.get('bookings', []):
        if is_active(booking) or booking.get('status') == 'allocated':
            _process_booking(document, booking)
    return save_document(document)


if __name__ == '__main__':
    run()

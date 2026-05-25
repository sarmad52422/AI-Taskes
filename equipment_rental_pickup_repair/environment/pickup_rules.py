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
        requested[equipment_id] = requested.get(equipment_id, 0) + int(item.get('units', 0))
    return requested


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


def available_for_booking(document, booking_id, equipment_id):
    unit = document.get('inventory', {}).get(equipment_id, {}).get('on_hand', 0)
    return int(unit) - active_external_allocated(document.get('reservations', []), booking_id, equipment_id)


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned

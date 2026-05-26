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
        requested[signer_role] = requested.get(signer_role, 0) + int(signer_request.get('slot_count', 0))
    return requested


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


def available_for_envelope(document, envelope_id, signer_role):
    capacity = document.get('role_capacity', {}).get(signer_role, {}).get('available_slots', 0)
    return int(capacity) - active_external_reserved(document.get('slots', []), envelope_id, signer_role)


def clear_stale_metadata(metadata):
    cleaned = dict(metadata or {})
    for key in STALE_KEYS:
        cleaned.pop(key, None)
    return cleaned

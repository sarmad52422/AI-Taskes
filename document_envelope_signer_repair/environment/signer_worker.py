from signer_store import load_document, save_document
from signer_rules import WORKER_OWNER, is_active, requested_quantities, available_for_envelope


def _find_slot(document, envelope_id, signer_role):
    for slot in document.get('slots', []):
        if slot.get('envelope_id') == envelope_id and slot.get('signer_role') == signer_role:
            return slot
    return None


def _reserve_signer_request(document, envelope, signer_role, slot_count):
    slot = _find_slot(document, envelope['id'], signer_role)
    if slot:
        slot['slot_count'] = slot_count
        slot['state'] = 'active'
        slot['owner'] = WORKER_OWNER
        return 'refreshed'
    document.setdefault('slots', []).append({
        'envelope_id': envelope['id'],
        'signer_role': signer_role,
        'slot_count': slot_count,
        'owner': WORKER_OWNER,
        'state': 'active',
        'source_attempt': envelope.get('attempts', 0),
    })
    return 'created'


def _process_envelope(document, envelope):
    requested = requested_quantities(envelope)
    for signer_role, slot_count in requested.items():
        if available_for_envelope(document, envelope['id'], signer_role) < slot_count:
            envelope['attempts'] = envelope.get('attempts', 0) + 1
            envelope['status'] = 'failed' if envelope['attempts'] >= envelope.get('max_attempts', 1) else 'retrying'
            envelope.setdefault('metadata', {})['retry_reason'] = 'signer_slot_unavailable'
            document.setdefault('stats', {})['failures'] = document.setdefault('stats', {}).get('failures', 0) + 1
            return
        action = _reserve_signer_request(document, envelope, signer_role, slot_count)
        key = 'slots_created' if action == 'created' else 'slots_refreshed'
        document.setdefault('stats', {})[key] = document.setdefault('stats', {}).get(key, 0) + 1
    envelope['status'] = 'ready'


def run():
    document = load_document()
    for envelope in document.get('envelopes', []):
        if is_active(envelope) or envelope.get('status') == 'ready':
            _process_envelope(document, envelope)
    return save_document(document)


if __name__ == '__main__':
    run()

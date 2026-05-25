from budget_store import load_state, save_state
from budget_rules import is_active


def run():
    doc = load_state()
    events = doc.setdefault('spend_events', {})
    ledger = doc.setdefault('budget_ledger', {})
    for job in doc.get('jobs', []):
        if not is_active(job):
            continue
        processed = []
        spend = 0
        for event_id in job.get('expected_event_ids', []):
            event = events.get(event_id)
            if not event:
                continue
            ledger[event_id] = {'event_id': event_id, 'campaign_id': event.get('campaign_id'), 'account_id': event.get('account_id'), 'window': event.get('window'), 'amount_cents': event.get('amount_cents', 0), 'owner_job_id': job.get('id'), 'source_version': event.get('version'), 'kind': 'exchange_spend'}
            processed.append(event_id)
            spend += event.get('amount_cents', 0)
        job['status'] = 'completed'
        job['attempts'] = job.get('attempts', 0) + 1
        job['processed_event_ids'] = processed
        job['summary'] = {'event_count': len(processed), 'spend_cents': spend, 'remaining_budget_cents': 0}
    return save_state(doc)

if __name__ == '__main__':
    run()

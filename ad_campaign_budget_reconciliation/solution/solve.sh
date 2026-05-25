#!/bin/bash
set -e

cat > /app/budget_rules.py <<'PY'
TERMINAL = {'completed', 'failed', 'cancelled'}
ACTIVE = {'pending', 'retrying'}

def is_active(job):
    return job.get('status') in ACTIVE

def unique_keep_order(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result

def next_attempt(job):
    return job.get('attempts', 0) + 1

def owner_is_terminal(owner_job_id, jobs):
    for job in jobs:
        if job.get('id') == owner_job_id:
            return job.get('status') in TERMINAL
    return False
PY

cat > /app/budget_store.py <<'PY'
import json
from pathlib import Path

STATE_PATH = Path('/app/ad_state.json')

def load_state(path=STATE_PATH):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)

def save_state(document, path=STATE_PATH):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write('\n')
    return document
PY

cat > /app/budget_worker.py <<'PY'
from budget_store import load_state, save_state
from budget_rules import is_active, unique_keep_order, next_attempt, owner_is_terminal


def _retry_or_fail(job, reason, message):
    job['attempts'] = next_attempt(job)
    job['retry_reason'] = reason
    job['last_error'] = message
    job['status'] = 'failed' if job['attempts'] >= job.get('max_attempts', 0) else 'retrying'


def _row(job, event_id, event):
    return {'event_id': event_id, 'campaign_id': event['campaign_id'], 'account_id': event['account_id'], 'window': event['window'], 'amount_cents': event.get('amount_cents', 0), 'owner_job_id': job['id'], 'source_version': event.get('version'), 'kind': 'exchange_spend'}


def run():
    doc = load_state()
    campaigns = doc.setdefault('campaigns', {})
    events = doc.setdefault('spend_events', {})
    ledger = doc.setdefault('budget_ledger', {})
    doc.setdefault('manual_holds', {})
    jobs = doc.get('jobs', [])
    for job in jobs:
        if not is_active(job):
            continue
        expected = unique_keep_order(job.get('expected_event_ids', []))
        campaign = campaigns.get(job.get('campaign_id'))
        source_errors = []
        conflict = None
        if not campaign or campaign.get('account_id') != job.get('account_id') or campaign.get('status') != 'active':
            source_errors.append(job.get('campaign_id'))
        else:
            for event_id in expected:
                event = events.get(event_id)
                if not event or event.get('campaign_id') != job.get('campaign_id') or event.get('account_id') != job.get('account_id') or event.get('window') != job.get('window'):
                    source_errors.append(event_id)
                    continue
                existing = ledger.get(event_id)
                if existing and existing.get('owner_job_id') != job.get('id') and not owner_is_terminal(existing.get('owner_job_id'), jobs):
                    conflict = event_id
                    break
        if source_errors:
            _retry_or_fail(job, 'source_unavailable', 'unavailable events: ' + ','.join(source_errors))
            continue
        if conflict:
            _retry_or_fail(job, 'ledger_conflict', 'protected ledger row: ' + conflict)
            continue
        spend = 0
        for event_id in expected:
            event = events[event_id]
            spend += event.get('amount_cents', 0)
            existing = ledger.get(event_id)
            if not (existing and existing.get('owner_job_id') == job.get('id') and existing.get('source_version') == event.get('version')):
                ledger[event_id] = _row(job, event_id, event)
        job['status'] = 'completed'
        job['processed_event_ids'] = expected
        job['summary'] = {'event_count': len(expected), 'spend_cents': spend, 'remaining_budget_cents': campaign.get('daily_budget_cents', 0) - spend}
        job.pop('last_error', None)
        job.pop('retry_reason', None)
    return save_state(doc)

if __name__ == '__main__':
    run()
PY

#!/bin/bash
set -e

cat > /app/manifest_rules.py <<'PY'
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

def unique_packages(values):
    return unique_keep_order(values or [])

def next_attempt(job):
    return job.get('attempts', 0) + 1

def owner_is_terminal(owner_job_id, jobs):
    for job in jobs:
        if job.get('id') == owner_job_id:
            return job.get('status') in TERMINAL
    return False
PY

cat > /app/manifest_store.py <<'PY'
import json
from pathlib import Path

STATE_PATH = Path('/app/route_state.json')

def load_state(path=STATE_PATH):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)

def save_state(document, path=STATE_PATH):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write('\n')
    return document
PY

cat > /app/manifest_worker.py <<'PY'
from manifest_store import load_state, save_state
from manifest_rules import is_active, unique_keep_order, unique_packages, next_attempt, owner_is_terminal


def _retry_or_fail(job, reason, message):
    job['attempts'] = next_attempt(job)
    job['retry_reason'] = reason
    job['last_error'] = message
    job['status'] = 'failed' if job['attempts'] >= job.get('max_attempts', 0) else 'retrying'


def _audit_exists(audit, row):
    return any(existing == row for existing in audit)


def _row(job, stop_id, stop):
    return {'stop_id': stop_id, 'route_id': stop['route_id'], 'driver_id': job['driver_id'], 'package_ids': unique_packages(stop.get('packages', [])), 'owner_job_id': job['id'], 'source_version': stop.get('scan_version'), 'status': 'manifested'}


def run():
    doc = load_state()
    stops = doc.setdefault('stops', {})
    manifests = doc.setdefault('manifests', {})
    audit = doc.setdefault('audit_log', [])
    jobs = doc.get('jobs', [])
    for job in jobs:
        if not is_active(job):
            continue
        expected = unique_keep_order(job.get('expected_stop_ids', []))
        source_errors = []
        conflict = None
        for stop_id in expected:
            stop = stops.get(stop_id)
            if not stop or stop.get('route_id') != job.get('route_id') or stop.get('driver_id') != job.get('driver_id'):
                source_errors.append(stop_id)
                continue
            existing = manifests.get(stop_id)
            if existing and existing.get('owner_job_id') != job.get('id') and not owner_is_terminal(existing.get('owner_job_id'), jobs):
                conflict = stop_id
                break
        if source_errors:
            _retry_or_fail(job, 'source_unavailable', 'unavailable stops: ' + ','.join(source_errors))
            continue
        if conflict:
            _retry_or_fail(job, 'manifest_conflict', 'protected manifest row: ' + conflict)
            continue
        package_count = 0
        for stop_id in expected:
            stop = stops[stop_id]
            package_ids = unique_packages(stop.get('packages', []))
            package_count += len(package_ids)
            existing = manifests.get(stop_id)
            if not (existing and existing.get('owner_job_id') == job.get('id') and existing.get('source_version') == stop.get('scan_version')):
                manifests[stop_id] = _row(job, stop_id, stop)
            audit_row = {'job_id': job['id'], 'stop_id': stop_id, 'event': 'manifested', 'source_version': stop.get('scan_version')}
            if not _audit_exists(audit, audit_row):
                audit.append(audit_row)
        job['status'] = 'completed'
        job['touched_stop_ids'] = expected
        job['summary'] = {'stop_count': len(expected), 'package_count': package_count}
        job.pop('last_error', None)
        job.pop('retry_reason', None)
    return save_state(doc)

if __name__ == '__main__':
    run()
PY

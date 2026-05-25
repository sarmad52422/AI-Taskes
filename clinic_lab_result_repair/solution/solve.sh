#!/bin/bash
set -e

cat > /app/lab_store.py <<'PY'
import json
from pathlib import Path

DATA_PATH = Path('/app/lab_data.json')

def load_document():
    with DATA_PATH.open() as f:
        return json.load(f)

def save_document(document):
    with DATA_PATH.open('w') as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write('\n')
    return document
PY

cat > /app/lab_rules.py <<'PY'
ACTIVE_STATUSES = {'queued', 'retrying'}
TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}

def is_active(job):
    return job.get('status') in ACTIVE_STATUSES

def is_protecting_status(status):
    return status in ACTIVE_STATUSES or status == 'completed'

def sample_for(document, patient_id, test_code):
    return document.get('samples', {}).get(patient_id, {}).get(test_code)

def make_row(job, test_code, sample):
    return {
        'patient_id': job['patient_id'],
        'test_code': test_code,
        'value': sample['value'],
        'unit': sample.get('unit'),
        'source_version': sample.get('version'),
        'owner_job_id': job['job_id'],
    }

def row_matches(row, wanted):
    return all(row.get(k) == v for k, v in wanted.items())

def active_failure(job, reason):
    attempts = int(job.get('attempts', 0)) + 1
    job['attempts'] = attempts
    job['status'] = 'failed' if attempts >= int(job.get('max_attempts', 1)) else 'retrying'
    metadata = job.setdefault('metadata', {})
    metadata['retry_reason'] = reason
PY

cat > /app/lab_worker.py <<'PY'
from lab_store import load_document, save_document
from lab_rules import is_active, is_protecting_status, sample_for, make_row, row_matches, active_failure


def _owner_status(document):
    return {job.get('job_id'): job.get('status') for job in document.get('jobs', [])}


def _clear_retry_metadata(job):
    metadata = job.setdefault('metadata', {})
    for key in ('retry_reason', 'last_error'):
        metadata.pop(key, None)


def _process_job(document, job):
    rows = document.setdefault('result_rows', [])
    required = list(dict.fromkeys(job.get('required_tests', [])))
    patient_id = job.get('patient_id')
    owner = job.get('job_id')
    statuses = _owner_status(document)

    wanted = []
    for code in required:
        sample = sample_for(document, patient_id, code)
        if sample is None:
            active_failure(job, 'missing_sample')
            return
        wanted.append(make_row(job, code, sample))

    for wanted_row in wanted:
        for row in rows:
            same_patient_test = row.get('patient_id') == patient_id and row.get('test_code') == wanted_row['test_code']
            if not same_patient_test or row.get('owner_job_id') == owner:
                continue
            if is_protecting_status(statuses.get(row.get('owner_job_id'))):
                active_failure(job, 'protected_result_conflict')
                return

    new_rows = []
    required_set = set(required)
    wanted_by_code = {row['test_code']: row for row in wanted}
    seen_owned = set()

    for row in rows:
        owned = row.get('owner_job_id') == owner and row.get('patient_id') == patient_id
        if not owned:
            new_rows.append(row)
            continue
        code = row.get('test_code')
        if code not in required_set:
            continue
        wanted_row = wanted_by_code[code]
        if code in seen_owned:
            continue
        new_rows.append(row if row_matches(row, wanted_row) else wanted_row)
        seen_owned.add(code)

    for code in required:
        if code not in seen_owned:
            new_rows.append(wanted_by_code[code])

    document['result_rows'] = new_rows
    job['status'] = 'completed'
    _clear_retry_metadata(job)


def run():
    document = load_document()
    for job in document.get('jobs', []):
        if is_active(job):
            _process_job(document, job)
    return save_document(document)

if __name__ == '__main__':
    run()
PY

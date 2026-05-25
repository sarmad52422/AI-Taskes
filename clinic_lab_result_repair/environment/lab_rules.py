ACTIVE_STATUSES = {'queued', 'retrying'}
TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}

def is_active(job):
    return job.get('status') in ACTIVE_STATUSES

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

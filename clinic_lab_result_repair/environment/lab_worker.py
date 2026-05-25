from lab_store import load_document, save_document
from lab_rules import sample_for, make_row


def run():
    document = load_document()
    rows = document.setdefault('result_rows', [])
    for job in document.get('jobs', []):
        if job.get('status') == 'failed':
            continue
        required = job.get('required_tests', [])
        made = []
        for code in required:
            sample = sample_for(document, job.get('patient_id'), code)
            if sample:
                made.append(make_row(job, code, sample))
        if len(made) != len(required):
            job['attempts'] = job.get('attempts', 0) + 1
            job['status'] = 'retrying'
            job.setdefault('metadata', {})['retry_reason'] = 'missing_sample'
            rows.extend(made)
        else:
            job['status'] = 'completed'
            job['attempts'] = job.get('attempts', 0) + 1
            job['metadata'] = {}
            rows.extend(made)
    return save_document(document)

if __name__ == '__main__':
    run()

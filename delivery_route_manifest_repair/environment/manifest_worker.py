from manifest_store import load_state, save_state
from manifest_rules import is_active


def run():
    doc = load_state()
    stops = doc.setdefault('stops', {})
    manifests = doc.setdefault('manifests', {})
    audit = doc.setdefault('audit_log', [])
    for job in doc.get('jobs', []):
        if not is_active(job):
            continue
        touched = []
        for stop_id in job.get('expected_stop_ids', []):
            stop = stops.get(stop_id)
            if not stop:
                continue
            manifests[stop_id] = {
                'stop_id': stop_id,
                'route_id': stop.get('route_id'),
                'driver_id': job.get('driver_id'),
                'package_ids': list(stop.get('packages', [])),
                'owner_job_id': job.get('id'),
                'source_version': stop.get('scan_version'),
                'status': 'manifested',
            }
            audit.append({'job_id': job.get('id'), 'stop_id': stop_id, 'event': 'manifested', 'source_version': stop.get('scan_version')})
            touched.append(stop_id)
        job['status'] = 'completed'
        job['attempts'] = job.get('attempts', 0) + 1
        job['touched_stop_ids'] = touched
        job['summary'] = {'stop_count': len(touched), 'package_count': sum(len(stops[s].get('packages', [])) for s in touched if s in stops)}
    return save_state(doc)

if __name__ == '__main__':
    run()

TERMINAL = {'completed', 'failed', 'cancelled'}

def is_active(job):
    return job.get('status') in {'pending', 'retrying'}

def unique_keep_order(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result

def can_retry(job):
    return job.get('attempts', 0) < job.get('max_attempts', 0)

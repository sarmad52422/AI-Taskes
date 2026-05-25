TERMINAL = {'completed', 'failed', 'cancelled'}

def is_active(job):
    return job.get('status') in {'pending', 'retrying'}

def unique_keep_order(values):
    output = []
    for value in values:
        if value not in output:
            output.append(value)
    return output

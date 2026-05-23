#!/bin/bash
set -euo pipefail

if command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  PYTHON_BIN=python3
fi

"$PYTHON_BIN" - <<'PY'
from pathlib import Path


queue_path = Path("app/queue_manager.py")
queue_source = queue_path.read_text(encoding="utf-8")
queue_source = queue_source.replace("MAX_RETRIES = 3", "MAX_RETRIES = 2")
queue_source = queue_source.replace(
    'job.get("status") in {"pending", "retrying", "completed"}',
    'job.get("status") in {"pending", "retrying"}',
)
queue_source = queue_source.replace(
    '''def record_retry(job):
    retries = job.get("retries", 0) + 1
    job["retries"] = retries
    job["status"] = "retrying"
''',
    '''def record_retry(job):
    retries = job.get("retries", 0) + 1
    job["retries"] = retries
    job["status"] = "failed" if retries >= MAX_RETRIES else "retrying"
    job.pop("summary", None)
''',
)
queue_path.write_text(queue_source, encoding="utf-8")


pipeline_path = Path("app/pipeline.py")
pipeline_source = pipeline_path.read_text(encoding="utf-8")
if "import re" not in pipeline_source:
    pipeline_source = pipeline_source.replace("import json\n", "import json\nimport re\n")

old_parse = '''def parse_model_response(response):
    parsed = json.loads(response)
    return parsed["summary"], parsed.get("skill")
'''

new_parse = '''def _split_loose_fields(response):
    fields = {}
    body = response.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]

    for part in body.split(","):
        key, separator, value = part.partition(":")
        if not separator:
            continue
        key = key.strip().strip("'\\\\\\"")
        value = value.strip().strip("'\\\\\\"")
        if key and value:
            fields[key] = value
    return fields


def parse_model_response(response):
    try:
        parsed = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        parsed = _split_loose_fields(response) if isinstance(response, str) else {}

    if not isinstance(parsed, dict):
        return None

    summary = parsed.get("summary")
    if not isinstance(summary, str):
        return None

    summary = re.sub(r"\\s+", " ", summary).strip()
    if not summary:
        return None

    skill = parsed.get("skill")
    return {
        "summary": summary,
        "skill": skill.strip() if isinstance(skill, str) and skill.strip() else None,
    }
'''

pipeline_source = pipeline_source.replace(old_parse, new_parse)

old_loop = '''    for job in claimable_jobs(jobs):
        try:
            summary, skill = parse_model_response(model_client.summarize(job))
        except Exception:
            record_retry(job)
            continue

        mark_completed(job, summary)
        if skill:
            job["skill"] = skill
'''

new_loop = '''    for job in claimable_jobs(jobs):
        parsed = parse_model_response(model_client.summarize(job))
        if parsed is None:
            record_retry(job)
            continue

        mark_completed(job, parsed["summary"])
        if parsed["skill"]:
            job["skill"] = parsed["skill"]
'''

pipeline_source = pipeline_source.replace(old_loop, new_loop)
pipeline_path.write_text(pipeline_source, encoding="utf-8")
PY

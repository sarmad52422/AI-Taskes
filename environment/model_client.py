import json


def generate_summary(job):
    job_id = job.get("id", "")
    events = job.get("events") or []

    if job_id == "job-json-alias":
        return json.dumps({
            "summary_text": "Player stayed calm under pressure",
            "skill_tag": "focus",
            "score": 0.82,
        })

    if job_id == "job-low-confidence":
        return "summary=Player rushed several decisions; skill=timing; confidence=0.42"

    if job_id == "job-retrying-empty" or not events:
        return "no usable coaching signal"

    if job_id == "job-already-complete":
        return "summary=Replacement summary; skill=unknown; confidence=0.99"

    return "Summary: Player improved timing | Skill: patience | Confidence: 0.86"

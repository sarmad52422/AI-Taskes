import json


def summarize(job):
    events = job.get("events", [])
    if not events:
        return "no session events available"

    if any(event.get("kind") == "structured" for event in events):
        return json.dumps(
            {
                "summary": "Player reflected on the session and chose a next step.",
                "skill": "reflection",
            }
        )

    return "{summary: Player improved timing, skill: patience}"

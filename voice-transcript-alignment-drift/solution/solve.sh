#!/bin/bash
set -euo pipefail

cat > /app/alignment_store.py <<'PY'
import hashlib
import json
from pathlib import Path


def load_document(app_dir):
    with (Path(app_dir) / "alignments.json").open() as handle:
        return json.load(handle)


def save_document(app_dir, document):
    with (Path(app_dir) / "alignments.json").open("w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def jobs(document):
    return document.setdefault("jobs", [])


def transcript_segments(document, audio_id):
    audio = document.setdefault("audio_files", {}).setdefault(audio_id, {})
    return audio.setdefault("segments", {})


def cache_record(document, audio_id, speaker):
    audio_cache = document.setdefault("alignment_cache", {}).setdefault(audio_id, {})
    return audio_cache.setdefault(speaker, {"aligned_count": 0, "segments": {}})


def existing_cache(document, audio_id, speaker):
    return document.get("alignment_cache", {}).get(audio_id, {}).get(speaker, {})


def missing_segments(document, job):
    segments = transcript_segments(document, job["audio_id"])
    return [segment_id for segment_id in job.get("segment_ids", []) if segment_id not in segments]


def alignment_id(job, segment_id):
    return f"{job['alignment_id']}:{segment_id}"


def score_for(text, speaker):
    digest = hashlib.sha1(f"{speaker}:{text}".encode()).digest()
    return round(int.from_bytes(digest[:2], "big") / 65535, 6)


def record_matches_segment(record, segment):
    return record.get("start_ms") == segment.get("start_ms") and record.get("end_ms") == segment.get("end_ms")


def conflicting_segments(document, job):
    aligned = existing_cache(document, job["audio_id"], job["speaker"]).get("segments", {})
    conflicts = []
    for segment_id in job.get("segment_ids", []):
        record = aligned.get(segment_id)
        if record and record.get("alignment_id") != alignment_id(job, segment_id):
            conflicts.append(segment_id)
    return conflicts


def existing_segments_for_job(document, job):
    segments = transcript_segments(document, job["audio_id"])
    aligned = existing_cache(document, job["audio_id"], job["speaker"]).get("segments", {})
    existing = []
    for segment_id in job.get("segment_ids", []):
        record = aligned.get(segment_id)
        if record and record.get("alignment_id") == alignment_id(job, segment_id) and record_matches_segment(record, segments[segment_id]):
            existing.append(segment_id)
    return existing


def normalize_alignment_ids(job, segment_ids):
    job["alignment_ids"] = [alignment_id(job, segment_id) for segment_id in segment_ids]


def align_segments(document, job):
    missing = missing_segments(document, job)
    if missing:
        return False, missing

    conflicts = conflicting_segments(document, job)
    if conflicts:
        return False, ["alignment_conflict"]

    segments = transcript_segments(document, job["audio_id"])
    existing = existing_segments_for_job(document, job)
    target = list(job.get("segment_ids", []))
    if len(existing) >= len(target):
        normalize_alignment_ids(job, target)
        return True, []

    cache = cache_record(document, job["audio_id"], job["speaker"])
    aligned = cache.setdefault("segments", {})
    for segment_id in target:
        current_id = alignment_id(job, segment_id)
        current_segment = segments[segment_id]
        current_record = aligned.get(segment_id)
        if current_record and current_record.get("alignment_id") == current_id and record_matches_segment(current_record, current_segment):
            continue
        aligned[segment_id] = {
            "alignment_id": current_id,
            "alignment_score": score_for(current_segment["text"], job["speaker"]),
            "speaker": job["speaker"],
            "start_ms": current_segment["start_ms"],
            "end_ms": current_segment["end_ms"],
            "write_count": int((current_record or {}).get("write_count", 0)) + 1,
        }
    cache["aligned_count"] = len(aligned)
    normalize_alignment_ids(job, target)
    return True, []
PY
cat > /app/alignment_rules.py <<'PY'
MAX_RETRIES = 3

PROCESSABLE_STATUSES = {"pending", "retrying"}
STALE_RETRY_METADATA = {"last_error", "retry_after", "retry_started_at", "next_retry_at"}


def should_process(job):
    return job.get("status") in PROCESSABLE_STATUSES


def failure_reason(job, missing):
    requested = len(job.get("segment_ids", []))
    if "alignment_conflict" in missing:
        return "alignment_conflict"
    if len(missing) == requested:
        return "segments_unavailable"
    return "partial_transcript"


def should_retry(job, reason):
    return int(job.get("retries", 0)) < MAX_RETRIES


def mark_retrying(job, reason):
    job["status"] = "retrying"
    job["retries"] = int(job.get("retries", 0)) + 1
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["retry_after"] = "next-run"
    metadata["last_attempt_status"] = "retrying"


def mark_failed(job, reason):
    job["status"] = "failed"
    job["failure_reason"] = reason
    metadata = job.setdefault("metadata", {})
    metadata["last_error"] = reason
    metadata["last_attempt_status"] = "failed"


def mark_aligned(job):
    job["status"] = "aligned"
    job.pop("failure_reason", None)
    metadata = job.setdefault("metadata", {})
    for key in STALE_RETRY_METADATA:
        metadata.pop(key, None)
    metadata["last_attempt_status"] = "aligned"
    metadata["aligned_at"] = "worker-run"
PY

cat > /app/alignment_worker.py <<'PY'
import os
from pathlib import Path

import alignment_rules
import alignment_store


def process_job(document, job):
    if not alignment_rules.should_process(job):
        return job

    ok, missing = alignment_store.align_segments(document, job)
    if ok:
        alignment_rules.mark_aligned(job)
        return job

    reason = alignment_rules.failure_reason(job, missing)
    if alignment_rules.should_retry(job, reason):
        alignment_rules.mark_retrying(job, reason)
    else:
        alignment_rules.mark_failed(job, reason)
    return job


def run(app_dir="/app"):
    document = alignment_store.load_document(app_dir)
    for job in alignment_store.jobs(document):
        process_job(document, job)
    alignment_store.save_document(app_dir, document)
    return document


def main():
    app_dir = Path(os.environ.get("ALIGNMENT_APP_DIR", "/app"))
    run(app_dir)


if __name__ == "__main__":
    main()
PY

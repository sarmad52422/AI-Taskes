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


def missing_segments(document, job):
    segments = transcript_segments(document, job["audio_id"])
    return [segment_id for segment_id in job.get("segment_ids", []) if segment_id not in segments]


def alignment_id(job, segment_id):
    return f"{job['alignment_id']}:{segment_id}"


def score_for(text, speaker):
    digest = hashlib.sha1(f"{speaker}:{text}".encode()).digest()
    return round(int.from_bytes(digest[:2], "big") / 65535, 6)


def align_segments(document, job):
    missing = missing_segments(document, job)
    if missing:
        return False, missing

    segments = transcript_segments(document, job["audio_id"])
    cache = cache_record(document, job["audio_id"], job["speaker"])
    aligned = cache.setdefault("segments", {})
    for segment_id in job.get("segment_ids", []):
        current_id = alignment_id(job, segment_id)
        existing = aligned.get(segment_id, {})
        aligned[segment_id] = {
            "alignment_id": current_id,
            "alignment_score": score_for(segments[segment_id]["text"], job["speaker"]),
            "speaker": job["speaker"],
            "start_ms": segments[segment_id]["start_ms"],
            "end_ms": segments[segment_id]["end_ms"],
            "write_count": int(existing.get("write_count", 0)) + 1,
        }
        cache["aligned_count"] = int(cache.get("aligned_count", 0)) + 1
        job.setdefault("alignment_ids", []).append(current_id)
    return True, []

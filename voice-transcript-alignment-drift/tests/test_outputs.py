import copy
import json
import os
import subprocess
import sys
from pathlib import Path

APP_SOURCE = Path(os.environ.get("APP_SOURCE_DIR", "/app"))


def write_document(path, document):
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def read_document(path):
    return json.loads(path.read_text())


def run_worker(tmp_path, document):
    write_document(tmp_path / "alignments.json", copy.deepcopy(document))
    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(APP_SOURCE)!r}); "
        "import alignment_worker; "
        f"result = alignment_worker.run({str(tmp_path)!r}); "
        "print(json.dumps(result, sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(APP_SOURCE),
        text=True,
        capture_output=True,
        check=True,
    )
    returned = json.loads(completed.stdout)
    persisted = read_document(tmp_path / "alignments.json")
    return returned, persisted


def base_job(status="pending", retries=0, segment_ids=None, alignment_id="align-1"):
    return {
        "alignment_id": alignment_id,
        "status": status,
        "retries": retries,
        "audio_id": "audio-a",
        "speaker": "speaker-a",
        "segment_ids": segment_ids or ["seg-1", "seg-2"],
        "alignment_ids": [],
        "metadata": {"source": "asr"},
    }


def audio_for(segments):
    return {"audio-a": {"segments": segments}}


def segment(text, start_ms=0, end_ms=1000):
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms}


def cache_for(segments=None, aligned_count=0):
    return {"audio-a": {"speaker-a": {"aligned_count": aligned_count, "segments": segments or {}}}}


def document_for(jobs, audio_files, cache=None):
    return {"jobs": jobs, "audio_files": audio_files, "alignment_cache": cache or {}}


def test_worker_returns_same_state_that_was_persisted(tmp_path):
    document = document_for([base_job(segment_ids=["seg-1"])], audio_for({"seg-1": segment("hello")}))
    returned, persisted = run_worker(tmp_path, document)
    assert returned == persisted


def test_pending_alignment_writes_segments(tmp_path):
    document = document_for(
        [base_job()],
        audio_for({"seg-1": segment("hello"), "seg-2": segment("world", 1100, 2000)}),
    )
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    cache = persisted["alignment_cache"]["audio-a"]["speaker-a"]
    assert job["status"] == "aligned"
    assert job["alignment_ids"] == ["align-1:seg-1", "align-1:seg-2"]
    assert cache["aligned_count"] == 2
    assert set(cache["segments"]) == {"seg-1", "seg-2"}
    assert cache["segments"]["seg-1"]["write_count"] == 1


def test_partial_transcript_moves_to_retrying_without_cache_write(tmp_path):
    document = document_for(
        [base_job(segment_ids=["seg-1", "seg-2"])],
        audio_for({"seg-1": segment("only one")}),
    )
    returned, persisted = run_worker(tmp_path, document)
    job = returned["jobs"][0]
    assert job["status"] == "retrying"
    assert job["retries"] == 1
    assert job["failure_reason"] == "partial_transcript"
    assert persisted["alignment_cache"] == {}


def test_retrying_alignment_recovers_when_segments_exist(tmp_path):
    job = base_job(status="retrying", retries=1, segment_ids=["seg-1"])
    job["failure_reason"] = "segments_unavailable"
    job["metadata"].update({"last_error": "segments_unavailable", "retry_after": "next-run"})
    document = document_for([job], audio_for({"seg-1": segment("now ready")}))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "aligned"
    assert returned["jobs"][0]["alignment_ids"] == ["align-1:seg-1"]
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"]["aligned_count"] == 1


def test_exhausted_retry_moves_to_failed(tmp_path):
    job = base_job(status="retrying", retries=3, segment_ids=["seg-1"])
    job["failure_reason"] = "segments_unavailable"
    document = document_for([job], audio_for({}))
    returned, persisted = run_worker(tmp_path, document)
    assert returned["jobs"][0]["status"] == "failed"
    assert returned["jobs"][0]["retries"] == 3
    assert returned["jobs"][0]["failure_reason"] == "segments_unavailable"
    assert persisted["alignment_cache"] == {}


def test_completed_alignment_remains_unchanged(tmp_path):
    job = base_job(status="completed", segment_ids=["seg-1"], alignment_id="done-1")
    job["alignment_ids"] = ["done-1:seg-1"]
    job["metadata"] = {"source": "asr", "completed_at": "2026-05-01T12:00:00Z"}
    cached = {
        "seg-1": {
            "alignment_id": "done-1:seg-1",
            "alignment_score": 0.8,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 1000,
            "write_count": 1,
        }
    }
    document = document_for([job], audio_for({"seg-1": segment("done")}), cache_for(cached, 1))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_failed_alignment_remains_unchanged(tmp_path):
    job = base_job(status="failed", retries=3, segment_ids=["seg-1"])
    job["failure_reason"] = "segments_unavailable"
    job["metadata"].update({"last_error": "segments_unavailable", "last_attempt_status": "failed"})
    document = document_for([job], audio_for({}))
    original = copy.deepcopy(document)
    returned, persisted = run_worker(tmp_path, document)
    assert returned == original
    assert persisted == original


def test_repeated_runs_do_not_change_aligned_count(tmp_path):
    document = document_for(
        [base_job(segment_ids=["seg-1", "seg-2"])],
        audio_for({"seg-1": segment("hello"), "seg-2": segment("world", 1000, 2000)}),
    )
    returned, persisted = run_worker(tmp_path, document)
    first_cache = copy.deepcopy(persisted["alignment_cache"]["audio-a"]["speaker-a"])
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["status"] == "aligned"
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"] == first_cache


def test_repeated_runs_do_not_duplicate_alignment_ids(tmp_path):
    document = document_for([base_job(segment_ids=["seg-1"])], audio_for({"seg-1": segment("hello")}))
    returned, persisted = run_worker(tmp_path, document)
    returned, persisted = run_worker(tmp_path, returned)
    assert returned["jobs"][0]["alignment_ids"] == ["align-1:seg-1"]
    assert list(persisted["alignment_cache"]["audio-a"]["speaker-a"]["segments"]) == ["seg-1"]


def test_retry_success_clears_stale_metadata_and_failure_state(tmp_path):
    job = base_job(status="retrying", retries=2, segment_ids=["seg-1"])
    job["failure_reason"] = "partial_transcript"
    job["metadata"].update(
        {
            "last_error": "partial_transcript",
            "retry_after": "next-run",
            "retry_started_at": "2026-05-20T09:00:00Z",
            "review_note": "keep speaker label",
        }
    )
    document = document_for([job], audio_for({"seg-1": segment("recovered")}))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert returned["jobs"][0]["status"] == "aligned"
    assert "failure_reason" not in returned["jobs"][0]
    assert "last_error" not in metadata
    assert "retry_after" not in metadata
    assert metadata["review_note"] == "keep speaker label"
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"]["aligned_count"] == 1


def test_retry_recovery_tops_up_existing_partial_alignment(tmp_path):
    job = base_job(status="retrying", retries=2, segment_ids=["seg-1", "seg-2", "seg-3"])
    job["alignment_ids"] = ["align-1:seg-1", "align-1:seg-old"]
    job["failure_reason"] = "partial_transcript"
    job["metadata"].update(
        {
            "last_error": "partial_transcript",
            "retry_after": "next-run",
            "review_note": "speaker diarization pass",
        }
    )
    existing = {
        "seg-1": {
            "alignment_id": "align-1:seg-1",
            "alignment_score": 0.77,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 900,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for(
            {
                "seg-1": segment("already aligned", 0, 900),
                "seg-2": segment("new segment", 1000, 1900),
                "seg-3": segment("another segment", 2000, 2900),
            }
        ),
        cache_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    cache = persisted["alignment_cache"]["audio-a"]["speaker-a"]
    assert recovered["status"] == "aligned"
    assert recovered["alignment_ids"] == ["align-1:seg-1", "align-1:seg-2", "align-1:seg-3"]
    assert "failure_reason" not in recovered
    assert "last_error" not in recovered["metadata"]
    assert recovered["metadata"]["review_note"] == "speaker diarization pass"
    assert cache["aligned_count"] == 3
    assert cache["segments"]["seg-1"] == existing["seg-1"]
    assert cache["segments"]["seg-2"]["write_count"] == 1


def test_retry_recovery_refreshes_stale_cached_timing(tmp_path):
    job = base_job(status="retrying", retries=1, segment_ids=["seg-1"])
    job["alignment_ids"] = ["align-1:seg-1"]
    job["failure_reason"] = "partial_transcript"
    stale = {
        "seg-1": {
            "alignment_id": "align-1:seg-1",
            "alignment_score": 0.11,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 800,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for({"seg-1": segment("timing changed", 500, 1700)}),
        cache_for(stale, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    cached = persisted["alignment_cache"]["audio-a"]["speaker-a"]["segments"]["seg-1"]
    assert recovered["status"] == "aligned"
    assert recovered["alignment_ids"] == ["align-1:seg-1"]
    assert cached["start_ms"] == 500
    assert cached["end_ms"] == 1700
    assert cached["write_count"] == 2
    assert "failure_reason" not in recovered


def test_alignment_owned_by_another_job_is_not_overwritten(tmp_path):
    job = base_job(status="pending", segment_ids=["seg-1"], alignment_id="align-new")
    existing = {
        "seg-1": {
            "alignment_id": "align-other:seg-1",
            "alignment_score": 0.66,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 900,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for({"seg-1": segment("already owned", 0, 900)}),
        cache_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    blocked = returned["jobs"][0]
    cache = persisted["alignment_cache"]["audio-a"]["speaker-a"]
    assert blocked["status"] == "retrying"
    assert blocked["retries"] == 1
    assert blocked["failure_reason"] == "alignment_conflict"
    assert blocked["alignment_ids"] == []
    assert cache["aligned_count"] == 1
    assert cache["segments"] == existing


def test_alignment_conflict_sets_retry_metadata(tmp_path):
    job = base_job(status="retrying", retries=1, segment_ids=["seg-1"], alignment_id="align-new")
    job["metadata"].update({"last_error": "partial_transcript", "retry_after": "next-run"})
    existing = {
        "seg-1": {
            "alignment_id": "align-other:seg-1",
            "alignment_score": 0.66,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 900,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for({"seg-1": segment("owned elsewhere", 0, 900)}),
        cache_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    blocked = returned["jobs"][0]
    assert blocked["status"] == "retrying"
    assert blocked["retries"] == 2
    assert blocked["failure_reason"] == "alignment_conflict"
    assert blocked["metadata"]["last_error"] == "alignment_conflict"
    assert blocked["metadata"]["retry_after"] == "next-run"
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"]["segments"] == existing


def test_exhausted_alignment_conflict_fails_without_overwrite(tmp_path):
    job = base_job(status="retrying", retries=3, segment_ids=["seg-1"], alignment_id="align-new")
    job["failure_reason"] = "alignment_conflict"
    existing = {
        "seg-1": {
            "alignment_id": "align-other:seg-1",
            "alignment_score": 0.66,
            "speaker": "speaker-a",
            "start_ms": 0,
            "end_ms": 900,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for({"seg-1": segment("owned elsewhere", 0, 900)}),
        cache_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    failed = returned["jobs"][0]
    assert failed["status"] == "failed"
    assert failed["retries"] == 3
    assert failed["failure_reason"] == "alignment_conflict"
    assert failed["metadata"]["last_error"] == "alignment_conflict"
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"]["segments"] == existing


def test_recovery_alignment_ids_follow_requested_segment_order(tmp_path):
    job = base_job(status="retrying", retries=1, segment_ids=["seg-1", "seg-2"])
    job["alignment_ids"] = ["align-1:seg-2"]
    job["failure_reason"] = "partial_transcript"
    existing = {
        "seg-2": {
            "alignment_id": "align-1:seg-2",
            "alignment_score": 0.55,
            "speaker": "speaker-a",
            "start_ms": 1000,
            "end_ms": 1800,
            "write_count": 1,
        }
    }
    document = document_for(
        [job],
        audio_for({"seg-1": segment("first", 0, 900), "seg-2": segment("second", 1000, 1800)}),
        cache_for(existing, 1),
    )
    returned, persisted = run_worker(tmp_path, document)
    recovered = returned["jobs"][0]
    cache = persisted["alignment_cache"]["audio-a"]["speaker-a"]
    assert recovered["status"] == "aligned"
    assert recovered["alignment_ids"] == ["align-1:seg-1", "align-1:seg-2"]
    assert cache["segments"]["seg-2"] == existing["seg-2"]
    assert cache["segments"]["seg-1"]["write_count"] == 1


def test_original_metadata_is_preserved_on_success(tmp_path):
    job = base_job(segment_ids=["seg-1"])
    job["metadata"] = {"source": "asr", "priority": "human-review"}
    document = document_for([job], audio_for({"seg-1": segment("metadata")}))
    returned, persisted = run_worker(tmp_path, document)
    metadata = returned["jobs"][0]["metadata"]
    assert metadata["source"] == "asr"
    assert metadata["priority"] == "human-review"
    assert persisted["alignment_cache"]["audio-a"]["speaker-a"]["aligned_count"] == 1

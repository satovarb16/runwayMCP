"""Tests for tools/jobs_store.py — persistence layer for analyzed job records.

Follows the same structure as test_profile_tools.py:
- monkeypatch _JOBS_PATH for isolation
- pass path= directly to helpers for unit-level tests
- @pytest.mark.contract for pydantic shape-pinning tests
- @pytest.mark.integration for server-registration tests (in test_server.py)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _patch_jobs_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _JOBS_PATH to a temp location, mirroring _patch_profile_path."""
    import tools.jobs_store as jobs_store_mod

    new_path = tmp_path / "jobs.json"
    monkeypatch.setattr(jobs_store_mod, "_JOBS_PATH", new_path)
    return new_path


def _make_job(**overrides) -> dict:
    """Factory for a minimal valid StoredJob-compatible dict."""
    base = {
        "url": "https://example.com/job/1",
        "title": "Software Engineer",
        "company": "Acme Corp",
        "visa_verdict": "GREEN",
        "analyzed_at": "2025-06-01T00:00:00+00:00",
        "applied": False,
        "score": None,
        "recommendation": None,
        "notes": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# T-01 / T-02: Pydantic model contract tests (SC-01, SC-02)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_stored_job_defaults():
    """SC-01: StoredJob with only required fields → optional fields default correctly."""
    from tools.jobs_store import StoredJob

    job = StoredJob(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        analyzed_at="2025-01-01T00:00:00Z",
    )

    assert job.applied is False
    assert job.score is None
    assert job.recommendation is None
    assert job.notes is None


@pytest.mark.contract
def test_stored_job_all_fields():
    """StoredJob stores all optional fields when provided."""
    from tools.jobs_store import StoredJob

    job = StoredJob(
        url="https://example.com/job/2",
        title="PM",
        company="Beta",
        visa_verdict="YELLOW",
        analyzed_at="2025-03-01T00:00:00Z",
        applied=True,
        score=75,
        recommendation="APPLY",
        notes="Looks good",
    )

    assert job.applied is True
    assert job.score == 75
    assert job.recommendation == "APPLY"
    assert job.notes == "Looks good"


@pytest.mark.contract
def test_job_store_default_empty():
    """SC-02: JobStore() with no arguments → jobs = []."""
    from tools.jobs_store import JobStore

    store = JobStore()

    assert store.jobs == []


@pytest.mark.contract
def test_save_job_result_shape():
    """SaveJobResult exposes the documented field names and defaults."""
    from tools.jobs_store import SaveJobResult

    result = SaveJobResult(success=True)

    assert result.success is True
    assert result.url is None
    assert result.updated is None
    assert result.storage_path is None
    assert result.error_message is None


@pytest.mark.contract
def test_list_jobs_result_shape():
    """ListJobsResult exposes the documented field names and defaults."""
    from tools.jobs_store import ListJobsResult

    result = ListJobsResult(success=True)

    assert result.success is True
    assert result.jobs == []
    assert result.count == 0
    assert result.error_message is None


@pytest.mark.contract
def test_mark_applied_result_shape():
    """MarkAppliedResult exposes the documented field names and defaults."""
    from tools.jobs_store import MarkAppliedResult

    result = MarkAppliedResult(success=False, error="not_found")

    assert result.success is False
    assert result.error == "not_found"
    assert result.url is None
    assert result.message is None


# ---------------------------------------------------------------------------
# T-03 / T-04: _read_jobs and _write_jobs (SC-16, SC-17 partial, SC-18, SC-19)
# ---------------------------------------------------------------------------


def test_read_jobs_missing_returns_empty_store(tmp_path):
    """SC-19: _read_jobs on a missing file returns JobStore(jobs=[]) — not an error."""
    from tools.jobs_store import _read_jobs, JobStore

    missing = tmp_path / "does_not_exist.json"
    result = _read_jobs(path=missing)

    assert isinstance(result, JobStore)
    assert result.jobs == []


def test_read_jobs_valid_file(tmp_path):
    """_read_jobs on a valid file returns the correct JobStore."""
    from tools.jobs_store import _read_jobs, JobStore

    jobs_path = tmp_path / "jobs.json"
    job_dict = _make_job()
    payload = {"jobs": [job_dict]}
    jobs_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _read_jobs(path=jobs_path)

    assert isinstance(result, JobStore)
    assert len(result.jobs) == 1
    assert result.jobs[0].url == job_dict["url"]


def test_read_jobs_corrupt_raises_value_error(tmp_path):
    """SC-16: _read_jobs on corrupt JSON raises ValueError mentioning 'corrupt'."""
    from tools.jobs_store import _read_jobs

    corrupt_path = tmp_path / "jobs.json"
    corrupt_path.write_text("not{json", encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)corrupt"):
        _read_jobs(path=corrupt_path)


def test_write_jobs_creates_parent_dirs(tmp_path):
    """_write_jobs creates parent directories if they do not exist."""
    from tools.jobs_store import _write_jobs, JobStore

    deep_path = tmp_path / "a" / "b" / "c" / "jobs.json"
    store = JobStore()

    _write_jobs(store, path=deep_path)

    assert deep_path.exists()


def test_write_jobs_writes_valid_json(tmp_path):
    """_write_jobs produces parseable JSON."""
    from tools.jobs_store import _write_jobs, JobStore, StoredJob

    jobs_path = tmp_path / "jobs.json"
    job = StoredJob(**_make_job())
    store = JobStore(jobs=[job])

    _write_jobs(store, path=jobs_path)

    raw = json.loads(jobs_path.read_text(encoding="utf-8"))
    assert "jobs" in raw
    assert len(raw["jobs"]) == 1


def test_write_jobs_round_trips(tmp_path):
    """_write_jobs followed by _read_jobs returns the same store."""
    from tools.jobs_store import _write_jobs, _read_jobs, JobStore, StoredJob

    jobs_path = tmp_path / "jobs.json"
    job = StoredJob(**_make_job(score=80))
    store = JobStore(jobs=[job])

    _write_jobs(store, path=jobs_path)
    result = _read_jobs(path=jobs_path)

    assert len(result.jobs) == 1
    assert result.jobs[0].score == 80


def test_write_jobs_overwrites_existing(tmp_path):
    """_write_jobs replaces an existing file."""
    from tools.jobs_store import _write_jobs, _read_jobs, JobStore, StoredJob

    jobs_path = tmp_path / "jobs.json"
    # Write initial store with one job
    job1 = StoredJob(**_make_job(url="https://example.com/1"))
    _write_jobs(JobStore(jobs=[job1]), path=jobs_path)

    # Overwrite with a different store
    job2 = StoredJob(**_make_job(url="https://example.com/2"))
    _write_jobs(JobStore(jobs=[job2]), path=jobs_path)

    result = _read_jobs(path=jobs_path)
    assert len(result.jobs) == 1
    assert result.jobs[0].url == "https://example.com/2"


def test_write_jobs_atomic_cleans_temp_on_error(tmp_path, monkeypatch):
    """SC-18: on write failure, temp file is unlinked and original is intact."""
    from tools.jobs_store import _write_jobs, StoredJob

    jobs_path = tmp_path / "jobs.json"
    original_content = json.dumps({"jobs": []})
    jobs_path.write_text(original_content, encoding="utf-8")

    # Force failure during write by making model_dump_json raise
    import tools.jobs_store as jobs_store_mod

    original_store = jobs_store_mod.JobStore

    class BrokenStore(original_store):
        def model_dump_json(self, **kwargs):
            raise RuntimeError("simulated write failure")

    job = StoredJob(**_make_job())
    broken = BrokenStore(jobs=[job])

    with pytest.raises(RuntimeError, match="simulated write failure"):
        _write_jobs(broken, path=jobs_path)

    # Original file is still intact
    assert jobs_path.read_text(encoding="utf-8") == original_content
    # No orphan temp files
    temp_files = list(tmp_path.glob(".jobs_tmp_*"))
    assert len(temp_files) == 0, f"Orphan temp files found: {temp_files}"


# ---------------------------------------------------------------------------
# T-05 / T-06: save_job_analysis (SC-03, SC-04, SC-05, SC-17)
# ---------------------------------------------------------------------------


def test_save_job_analysis_new_url_success(tmp_path, monkeypatch):
    """SC-03: save_job_analysis on new url → success=True, updated=False, file created."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        score=85,
        recommendation="APPLY",
    )

    assert result.success is True
    assert result.updated is False
    assert result.url == "https://example.com/job/1"
    assert result.storage_path is not None


def test_save_job_analysis_file_created_with_correct_fields(tmp_path, monkeypatch):
    """SC-03: saved record has applied=False, score, recommendation stored verbatim."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, _read_jobs

    save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        score=85,
        recommendation="APPLY",
    )

    store = _read_jobs(path=jobs_path)
    assert len(store.jobs) == 1
    record = store.jobs[0]
    assert record.applied is False
    assert record.score == 85
    assert record.recommendation == "APPLY"


def test_save_job_analysis_server_stamps_analyzed_at(tmp_path, monkeypatch):
    """SC-03: analyzed_at is a valid ISO-8601 string set by the server."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, _read_jobs
    from datetime import datetime

    save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
    )

    store = _read_jobs(path=jobs_path)
    analyzed_at = store.jobs[0].analyzed_at
    # Must be parseable as ISO-8601
    parsed = datetime.fromisoformat(analyzed_at)
    assert parsed is not None


def test_save_job_analysis_optional_fields_default_to_none(tmp_path, monkeypatch):
    """SC-04: When optional fields are omitted, stored record has None values."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, _read_jobs

    save_job_analysis(
        url="https://example.com/job/2",
        title="PM",
        company="Beta",
        visa_verdict="YELLOW",
    )

    store = _read_jobs(path=jobs_path)
    record = store.jobs[0]
    assert record.score is None
    assert record.recommendation is None
    assert record.notes is None


def test_save_job_analysis_upsert_same_url(tmp_path, monkeypatch):
    """SC-05: Upsert same url → success=True, updated=True, single record, latest wins."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, _read_jobs

    save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer I",
        company="Acme",
        visa_verdict="GREEN",
        score=70,
    )
    result = save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer II",
        company="Acme",
        visa_verdict="GREEN",
        score=90,
    )

    assert result.success is True
    assert result.updated is True

    store = _read_jobs(path=jobs_path)
    assert len(store.jobs) == 1
    assert store.jobs[0].score == 90
    assert store.jobs[0].title == "Engineer II"


def test_save_job_analysis_corrupt_store_returns_error(tmp_path, monkeypatch):
    """SC-17: corrupt store blocks save → success=False, error_message contains 'corrupt'."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    jobs_path.write_text("not{json", encoding="utf-8")
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
    )

    assert result.success is False
    assert result.error_message is not None
    assert re.search("(?i)corrupt", result.error_message)
    # SC-17: the corrupt file must be left untouched (no auto-repair / data loss).
    assert jobs_path.read_text(encoding="utf-8") == "not{json"


def test_save_job_analysis_storage_path_matches_injected(tmp_path, monkeypatch):
    """SC-03: storage_path in result matches the injected path string."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
    )

    assert result.storage_path == str(jobs_path.resolve())


# ---------------------------------------------------------------------------
# T-07 / T-08: list_jobs (SC-06..SC-12, SC-16, SC-20)
# ---------------------------------------------------------------------------


def test_list_jobs_missing_store_returns_empty(tmp_path, monkeypatch):
    """SC-06: list_jobs on missing store → success=True, count=0, jobs=[]."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs

    result = list_jobs()

    assert result.success is True
    assert result.count == 0
    assert result.jobs == []


def test_list_jobs_since_filter(tmp_path, monkeypatch):
    """SC-07: since filter excludes jobs before cutoff date."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(
        **_make_job(url="https://a.com", analyzed_at="2025-01-01T00:00:00Z")
    )
    job_b = StoredJob(
        **_make_job(url="https://b.com", analyzed_at="2025-06-01T00:00:00Z")
    )
    _write_jobs(JobStore(jobs=[job_a, job_b]), path=jobs_path)

    result = list_jobs(since="2025-03-01T00:00:00Z")

    assert result.success is True
    assert result.count == 1
    assert result.jobs[0].url == "https://b.com"


def test_list_jobs_applied_true_filter(tmp_path, monkeypatch):
    """SC-08: applied=True returns only applied jobs."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(**_make_job(url="https://a.com", applied=True))
    job_b = StoredJob(**_make_job(url="https://b.com", applied=False))
    _write_jobs(JobStore(jobs=[job_a, job_b]), path=jobs_path)

    result = list_jobs(applied=True)

    assert result.success is True
    assert result.count == 1
    assert result.jobs[0].url == "https://a.com"


def test_list_jobs_applied_false_filter(tmp_path, monkeypatch):
    """SC-09: applied=False returns only non-applied jobs."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(**_make_job(url="https://a.com", applied=True))
    job_b = StoredJob(**_make_job(url="https://b.com", applied=False))
    _write_jobs(JobStore(jobs=[job_a, job_b]), path=jobs_path)

    result = list_jobs(applied=False)

    assert result.success is True
    assert result.count == 1
    assert result.jobs[0].url == "https://b.com"


def test_list_jobs_min_score_filter(tmp_path, monkeypatch):
    """SC-10: min_score excludes score=None and below-threshold scores."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(**_make_job(url="https://a.com", score=90))
    job_b = StoredJob(**_make_job(url="https://b.com", score=50))
    job_c = StoredJob(**_make_job(url="https://c.com", score=None))
    _write_jobs(JobStore(jobs=[job_a, job_b, job_c]), path=jobs_path)

    result = list_jobs(min_score=60)

    assert result.success is True
    assert result.count == 1
    assert result.jobs[0].url == "https://a.com"


def test_list_jobs_sort_by_score_descending_none_last(tmp_path, monkeypatch):
    """SC-11: sort_by='score' → descending, score=None entries last."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(**_make_job(url="https://a.com", score=70))
    job_b = StoredJob(**_make_job(url="https://b.com", score=90))
    job_c = StoredJob(**_make_job(url="https://c.com", score=None))
    _write_jobs(JobStore(jobs=[job_a, job_b, job_c]), path=jobs_path)

    result = list_jobs(sort_by="score")

    assert result.success is True
    assert result.count == 3
    assert result.jobs[0].url == "https://b.com"  # score=90 first
    assert result.jobs[1].url == "https://a.com"  # score=70 second
    assert result.jobs[2].url == "https://c.com"  # score=None last


def test_list_jobs_limit_caps_count(tmp_path, monkeypatch):
    """SC-12: limit caps the result to at most N records after sort."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    jobs = [
        StoredJob(
            **_make_job(
                url=f"https://example.com/{i}",
                analyzed_at=f"2025-0{i + 1}-01T00:00:00Z",
            )
        )
        for i in range(5)
    ]
    _write_jobs(JobStore(jobs=jobs), path=jobs_path)

    result = list_jobs(limit=3)

    assert result.success is True
    assert result.count == 3
    assert len(result.jobs) == 3


def test_list_jobs_default_sort_newest_first(tmp_path, monkeypatch):
    """SC-20: default sort is analyzed_at descending (newest first)."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    job_a = StoredJob(
        **_make_job(url="https://a.com", analyzed_at="2025-01-01T00:00:00Z")
    )
    job_b = StoredJob(
        **_make_job(url="https://b.com", analyzed_at="2025-06-01T00:00:00Z")
    )
    _write_jobs(JobStore(jobs=[job_a, job_b]), path=jobs_path)

    result = list_jobs()

    assert result.success is True
    assert result.jobs[0].url == "https://b.com"  # newer first
    assert result.jobs[1].url == "https://a.com"


def test_list_jobs_corrupt_store_returns_error(tmp_path, monkeypatch):
    """SC-16: list_jobs on corrupt store → success=False, error_message mentions corrupt."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    jobs_path.write_text("not{json", encoding="utf-8")
    from tools.jobs_store import list_jobs

    result = list_jobs()

    assert result.success is False
    assert result.error_message is not None
    assert re.search("(?i)corrupt", result.error_message)
    # SC-16: list is read-only — the corrupt file must remain byte-for-byte intact.
    assert jobs_path.read_text(encoding="utf-8") == "not{json"


def test_list_jobs_invalid_sort_by_returns_error(tmp_path, monkeypatch):
    """Unknown sort_by value → success=False, error_message."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore

    _write_jobs(JobStore(jobs=[]), path=jobs_path)

    result = list_jobs(sort_by="bogus_field")

    assert result.success is False
    assert result.error_message is not None
    assert "bogus_field" in result.error_message


# ---------------------------------------------------------------------------
# T-09 / T-10: mark_applied (SC-13, SC-14, SC-15)
# ---------------------------------------------------------------------------


def test_mark_applied_sets_applied_true(tmp_path, monkeypatch):
    """SC-13: mark_applied → applied=True, existing notes unchanged."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import (
        mark_applied,
        _write_jobs,
        _read_jobs,
        JobStore,
        StoredJob,
    )

    job = StoredJob(**_make_job(url="https://example.com/job/1", notes="keep me"))
    _write_jobs(JobStore(jobs=[job]), path=jobs_path)

    result = mark_applied(url="https://example.com/job/1")

    assert result.success is True
    assert result.url == "https://example.com/job/1"

    store = _read_jobs(path=jobs_path)
    record = store.jobs[0]
    assert record.applied is True
    assert record.notes == "keep me"


def test_mark_applied_with_notes_updates_notes(tmp_path, monkeypatch):
    """SC-14: mark_applied with notes → applied=True and notes updated."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import (
        mark_applied,
        _write_jobs,
        _read_jobs,
        JobStore,
        StoredJob,
    )

    job = StoredJob(**_make_job(url="https://example.com/job/1"))
    _write_jobs(JobStore(jobs=[job]), path=jobs_path)

    result = mark_applied(url="https://example.com/job/1", notes="Applied via LinkedIn")

    assert result.success is True

    store = _read_jobs(path=jobs_path)
    record = store.jobs[0]
    assert record.applied is True
    assert record.notes == "Applied via LinkedIn"


def test_mark_applied_unknown_url_returns_not_found(tmp_path, monkeypatch):
    """SC-15: mark_applied on unknown url → success=False, error='not_found', no phantom."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import mark_applied, _write_jobs, _read_jobs, JobStore

    _write_jobs(JobStore(jobs=[]), path=jobs_path)

    result = mark_applied(url="https://example.com/missing")

    assert result.success is False
    assert result.error == "not_found"

    store = _read_jobs(path=jobs_path)
    assert len(store.jobs) == 0


def test_mark_applied_corrupt_store_returns_error(tmp_path, monkeypatch):
    """mark_applied on corrupt store → success=False with error info."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    jobs_path.write_text("not{json", encoding="utf-8")
    from tools.jobs_store import mark_applied

    result = mark_applied(url="https://example.com/job/1")

    assert result.success is False
    # error or message should be populated
    assert result.error is not None or result.message is not None


# ---------------------------------------------------------------------------
# FIX 1: since filter timezone-format mismatch
# ---------------------------------------------------------------------------


def test_list_jobs_since_z_suffix_vs_plus00_stored(tmp_path, monkeypatch):
    """FIX-1a: since='...Z' must include records stored with '+00:00' suffix at the same instant.

    The bug: lexicographic compare '2025-06-01T12:00:00+00:00' >= '2025-06-01T12:00:00Z'
    is False because '+' (43) < 'Z' (90). Proper datetime-aware compare fixes this.
    """
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    # Stored with +00:00 suffix (as server stamps them)
    job = StoredJob(
        **_make_job(url="https://boundary.com", analyzed_at="2025-06-01T12:00:00+00:00")
    )
    _write_jobs(JobStore(jobs=[job]), path=jobs_path)

    # since uses Z suffix for the SAME instant — raw string compare would wrongly EXCLUDE this
    result = list_jobs(since="2025-06-01T12:00:00Z")

    assert result.success is True
    assert result.count == 1  # must be INCLUDED (boundary is inclusive)


def test_list_jobs_since_invalid_returns_error(tmp_path, monkeypatch):
    """FIX-1b: since='garbage' → success=False, no exception raised."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs

    result = list_jobs(since="garbage")

    assert result.success is False
    assert result.error_message is not None
    assert (
        "since" in result.error_message.lower()
        or "timestamp" in result.error_message.lower()
    )


def test_list_jobs_since_corrupt_stored_timestamp_returns_error(tmp_path, monkeypatch):
    """FIX-1 regression: a malformed stored analyzed_at must NOT raise to the MCP
    boundary when a `since` filter is active — it returns an error envelope."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    payload = {"jobs": [_make_job(analyzed_at="not-a-timestamp")]}
    jobs_path.write_text(json.dumps(payload), encoding="utf-8")
    from tools.jobs_store import list_jobs

    # Must return an envelope, never raise.
    result = list_jobs(since="2020-01-01T00:00:00Z")

    assert result.success is False
    assert result.error_message is not None
    assert "timestamp" in result.error_message.lower()


# ---------------------------------------------------------------------------
# FIX 2: StoredJob construction outside try block
# ---------------------------------------------------------------------------


def test_save_job_analysis_invalid_score_returns_error(tmp_path, monkeypatch):
    """FIX-2: invalid score (float with fraction) → success=False, no exception raised to boundary."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis

    # score=85.5 is rejected by pydantic (int_from_float) — must NOT propagate
    result = save_job_analysis(
        url="https://example.com/job/invalid",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        score=85.5,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.error_message is not None


# ---------------------------------------------------------------------------
# FIX 3: upsert must preserve `applied`
# ---------------------------------------------------------------------------


def test_save_job_analysis_upsert_preserves_applied(tmp_path, monkeypatch):
    """FIX-3: upsert after mark_applied must NOT reset applied=False (data loss bug)."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, mark_applied, _read_jobs

    save_job_analysis(
        url="https://example.com/job/applied",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        score=70,
    )
    mark_applied(url="https://example.com/job/applied")

    # Re-analyze same job — applied must NOT be reset
    result = save_job_analysis(
        url="https://example.com/job/applied",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
        score=90,
    )

    assert result.success is True
    assert result.updated is True

    store = _read_jobs(path=jobs_path)
    assert len(store.jobs) == 1
    assert store.jobs[0].applied is True  # preserved — not reset to False
    assert store.jobs[0].score == 90  # new analysis data is stored


# ---------------------------------------------------------------------------
# FIX 4: guard non-positive limit
# ---------------------------------------------------------------------------


def test_list_jobs_limit_zero_returns_error(tmp_path, monkeypatch):
    """FIX-4a: limit=0 → success=False."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs

    result = list_jobs(limit=0)

    assert result.success is False
    assert result.error_message is not None
    assert (
        "positive" in result.error_message.lower()
        or "limit" in result.error_message.lower()
    )


def test_list_jobs_limit_negative_returns_error(tmp_path, monkeypatch):
    """FIX-4b: limit=-1 → success=False (not N-1 silent wrong result)."""
    _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs

    result = list_jobs(limit=-1)

    assert result.success is False
    assert result.error_message is not None


# ---------------------------------------------------------------------------
# FIX 6: strengthen analyzed_at freshness assertion
# ---------------------------------------------------------------------------


def test_save_job_analysis_server_stamps_analyzed_at_freshness(tmp_path, monkeypatch):
    """FIX-6: analyzed_at is not only parseable but is within 5 seconds of now (timezone-aware)."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import save_job_analysis, _read_jobs
    from datetime import datetime, timezone

    save_job_analysis(
        url="https://example.com/freshness",
        title="Engineer",
        company="Acme",
        visa_verdict="GREEN",
    )

    store = _read_jobs(path=jobs_path)
    analyzed_at = store.jobs[0].analyzed_at
    parsed = datetime.fromisoformat(analyzed_at)
    # Ensure aware datetime for subtraction (server stamps with +00:00)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    assert abs((parsed - datetime.now(timezone.utc)).total_seconds()) < 5


# ---------------------------------------------------------------------------
# FIX 7: combined FILTER → SORT → LIMIT pipeline test
# ---------------------------------------------------------------------------


def test_list_jobs_combined_filter_sort_limit(tmp_path, monkeypatch):
    """FIX-7: FILTER(since+min_score) → SORT(score desc) → LIMIT(2) produces exact expected order."""
    jobs_path = _patch_jobs_path(monkeypatch, tmp_path)
    from tools.jobs_store import list_jobs, _write_jobs, JobStore, StoredJob

    # Jobs: varying analyzed_at, applied, score
    job_old_low = StoredJob(
        **_make_job(
            url="https://old-low.com",
            analyzed_at="2024-01-01T00:00:00+00:00",
            score=40,
            applied=False,
        )
    )
    job_new_low = StoredJob(
        **_make_job(
            url="https://new-low.com",
            analyzed_at="2025-06-01T00:00:00+00:00",
            score=50,
            applied=False,
        )
    )
    job_new_high_a = StoredJob(
        **_make_job(
            url="https://new-high-a.com",
            analyzed_at="2025-06-02T00:00:00+00:00",
            score=85,
            applied=False,
        )
    )
    job_new_high_b = StoredJob(
        **_make_job(
            url="https://new-high-b.com",
            analyzed_at="2025-06-03T00:00:00+00:00",
            score=95,
            applied=True,
        )
    )
    _write_jobs(
        JobStore(jobs=[job_old_low, job_new_low, job_new_high_a, job_new_high_b]),
        path=jobs_path,
    )

    # since filters out job_old_low (2024); min_score=60 filters out job_new_low (50)
    # Remaining: new_high_a (85), new_high_b (95)
    # sort_by='score' desc: new_high_b (95), new_high_a (85)
    # limit=2 → both
    result = list_jobs(
        since="2025-01-01T00:00:00Z", min_score=60, sort_by="score", limit=2
    )

    assert result.success is True
    assert result.count == 2
    assert result.jobs[0].url == "https://new-high-b.com"  # score=95 first
    assert result.jobs[1].url == "https://new-high-a.com"  # score=85 second

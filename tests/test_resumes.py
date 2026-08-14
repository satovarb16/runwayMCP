"""Tests for tools/resumes.py — append-only resume version store.

Follows the same conventions as test_jobs_store.py:
- _patch_resumes_path(monkeypatch, tmp_path) for isolation
- path= passed directly to helpers for unit-level tests
- @pytest.mark.contract for pydantic shape-pinning tests

This store is append-only with parent_id lineage (unlike profile.py's
singleton overwrite or jobs_store.py's upsert-by-key): resume versions must
COEXIST, never destroy prior state. The module only persists raw text — it
never parses, structures, scores, or judges resume content (Option A).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _patch_resumes_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _RESUMES_PATH to a temp location, mirroring _patch_jobs_path."""
    import tools.resumes as resumes_mod

    new_path = tmp_path / "resumes.json"
    monkeypatch.setattr(resumes_mod, "_RESUMES_PATH", new_path)
    return new_path


# ---------------------------------------------------------------------------
# Model contract tests
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_resume_version_requires_explicit_parent_id():
    """parent_id has no default at the model level — every construction site
    must decide explicitly (spec: 'required, no default')."""
    from pydantic import ValidationError

    from tools.resumes import ResumeVersion

    with pytest.raises(ValidationError):
        ResumeVersion(
            id="v1",
            label="Base",
            content="text",
            created_at="2025-01-01T00:00:00+00:00",
        )

    # Passing parent_id=None explicitly is fine.
    version = ResumeVersion(
        id="v1",
        label="Base",
        content="text",
        parent_id=None,
        created_at="2025-01-01T00:00:00+00:00",
    )
    assert version.parent_id is None


@pytest.mark.contract
def test_resume_version_job_url_defaults_none():
    from tools.resumes import ResumeVersion

    version = ResumeVersion(
        id="v1",
        label="Base",
        content="text",
        parent_id=None,
        created_at="2025-01-01T00:00:00+00:00",
    )
    assert version.job_url is None


@pytest.mark.contract
def test_resume_store_default_empty():
    from tools.resumes import ResumeStore

    store = ResumeStore()
    assert store.versions == []


@pytest.mark.contract
def test_save_resume_version_result_shape():
    from tools.resumes import SaveResumeVersionResult

    result = SaveResumeVersionResult(success=True)

    assert result.success is True
    assert result.id is None
    assert result.label is None
    assert result.parent_id is None
    assert result.storage_path is None
    assert result.error is None
    assert result.message is None


@pytest.mark.contract
def test_get_resume_version_result_shape():
    from tools.resumes import GetResumeVersionResult

    result = GetResumeVersionResult(success=True)

    assert result.success is True
    assert result.version is None
    assert result.error is None
    assert result.message is None


@pytest.mark.contract
def test_list_resume_versions_result_shape():
    from tools.resumes import ListResumeVersionsResult

    result = ListResumeVersionsResult(success=True)

    assert result.success is True
    assert result.versions == []
    assert result.count == 0
    assert result.error_message is None


# ---------------------------------------------------------------------------
# SC-01: Contract — required fields, server-stamped id/created_at
# ---------------------------------------------------------------------------


def test_sc01_first_save_server_generates_id_and_created_at(tmp_path, monkeypatch):
    """SC-01: id and created_at are server-generated, parent_id=None, content
    stored verbatim."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="my resume text", parent_id=None)

    assert result.success is True
    assert result.id is not None
    assert result.id != ""

    from tools.resumes import get_resume_version

    fetched = get_resume_version(id=result.id)
    assert fetched.success is True
    assert fetched.version.parent_id is None
    assert fetched.version.content == "my resume text"
    assert fetched.version.created_at is not None
    assert fetched.version.created_at != ""


# ---------------------------------------------------------------------------
# R2: First saved version is the base — parent_id lineage rules
# ---------------------------------------------------------------------------


def test_sc02_first_save_on_empty_store_establishes_base(tmp_path, monkeypatch):
    """SC-02: save_resume_version(parent_id=None, ...) on an empty store
    succeeds and the stored record has parent_id=None."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="text", parent_id=None)

    assert result.success is True
    assert result.parent_id is None


def test_sc03_empty_store_rejects_nonnull_parent_id(tmp_path, monkeypatch):
    """SC-03: empty store + parent_id='some-id' -> error='invalid_parent'."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="text", parent_id="some-id")

    assert result.success is False
    assert result.error == "invalid_parent"


def test_sc04_nonempty_store_rejects_none_parent_id(tmp_path, monkeypatch):
    """SC-04: a base version already exists -> parent_id=None is rejected."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    first = save_resume_version(label="Base", content="text", parent_id=None)
    assert first.success is True

    result = save_resume_version(label="Second base?", content="text", parent_id=None)

    assert result.success is False
    assert result.error == "invalid_parent"


def test_sc05_dangling_parent_id_is_rejected(tmp_path, monkeypatch):
    """SC-05: an unknown parent_id -> error='parent_not_found', no record
    persisted."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    base = save_resume_version(label="Base", content="text", parent_id=None)
    assert base.success is True

    result = save_resume_version(label="Child", content="text", parent_id="unknown-id")

    assert result.success is False
    assert result.error == "parent_not_found"

    # No record persisted for the failed save.
    from tools.resumes import list_resume_versions

    listed = list_resume_versions()
    assert listed.count == 1
    assert listed.versions[0].id == base.id


# ---------------------------------------------------------------------------
# R3: Content is stored raw — no server-side fit validation
# ---------------------------------------------------------------------------


def test_sc06_arbitrary_content_stored_verbatim(tmp_path, monkeypatch):
    """SC-06: content persisted byte-for-byte, no length/format validation —
    including empty strings and multi-line/odd-format text."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version, save_resume_version

    weird_content = "line one\nline two\t\ttabbed\n\n---\n### markdown-ish ###\n"
    result = save_resume_version(label="Base", content=weird_content, parent_id=None)
    assert result.success is True

    fetched = get_resume_version(id=result.id)
    assert fetched.version.content == weird_content

    # Empty string content is not rejected — no format/length validation.
    child = save_resume_version(label="Empty", content="", parent_id=result.id)
    assert child.success is True
    fetched_child = get_resume_version(id=child.id)
    assert fetched_child.version.content == ""


# ---------------------------------------------------------------------------
# R4: get_resume_version retrieves by id or "latest"
# ---------------------------------------------------------------------------


def test_sc07_retrieve_by_exact_id(tmp_path, monkeypatch):
    """SC-07: get_resume_version(id='v2') returns the matching version."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version, save_resume_version

    base = save_resume_version(label="Base", content="base text", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2 text", parent_id=base.id)

    result = get_resume_version(id=v2.id)

    assert result.success is True
    assert result.version.id == v2.id
    assert result.version.label == "V2"


def test_sc08_unknown_id_returns_not_found(tmp_path, monkeypatch):
    """SC-08: get_resume_version(id='ghost') -> success=False, error='not_found'."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version, save_resume_version

    save_resume_version(label="Base", content="text", parent_id=None)

    result = get_resume_version(id="ghost")

    assert result.success is False
    assert result.error == "not_found"


def test_sc08_empty_store_get_by_id_returns_not_found(tmp_path, monkeypatch):
    """SC-08 (empty-store variant): no versions saved at all -> not_found."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version

    result = get_resume_version(id="anything")

    assert result.success is False
    assert result.error == "not_found"


def test_sc09_latest_returns_most_recently_created(tmp_path, monkeypatch):
    """SC-09: id='latest' returns the most recently created version (v3),
    not necessarily the base."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version, save_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2", parent_id=base.id)
    v3 = save_resume_version(label="V3", content="v3", parent_id=v2.id)

    result = get_resume_version(id="latest")

    assert result.success is True
    assert result.version.id == v3.id


def test_sc09_latest_on_empty_store_returns_not_found(tmp_path, monkeypatch):
    """id='latest' on an empty store has nothing to return -> not_found."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version

    result = get_resume_version(id="latest")

    assert result.success is False
    assert result.error == "not_found"


# ---------------------------------------------------------------------------
# R5: Lineage walk-up reaches the base
# ---------------------------------------------------------------------------


def test_sc10_walking_parent_id_from_tip_terminates_at_base(tmp_path, monkeypatch):
    """SC-10: base -> v2 -> v3. Walking v3.parent_id then that result's
    parent_id again terminates at the base after exactly 2 hops."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import get_resume_version, save_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2", parent_id=base.id)
    v3 = save_resume_version(label="V3", content="v3", parent_id=v2.id)

    tip = get_resume_version(id=v3.id)
    assert tip.success is True
    hop_1 = get_resume_version(id=tip.version.parent_id)
    assert hop_1.success is True
    assert hop_1.version.id == v2.id

    hop_2 = get_resume_version(id=hop_1.version.parent_id)
    assert hop_2.success is True
    assert hop_2.version.id == base.id
    assert hop_2.version.parent_id is None


# ---------------------------------------------------------------------------
# R6: list_resume_versions
# ---------------------------------------------------------------------------


def test_sc11_empty_store_returns_empty_list_no_error(tmp_path, monkeypatch):
    """SC-11: no resumes.json file exists -> success=True, versions=[], count=0."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import list_resume_versions

    result = list_resume_versions()

    assert result.success is True
    assert result.versions == []
    assert result.count == 0
    assert result.error_message is None


def test_sc12_results_sorted_newest_first(tmp_path, monkeypatch):
    """SC-12: base, v2, v3 saved in that order -> listed as [v3, v2, base]."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import list_resume_versions, save_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2", parent_id=base.id)
    v3 = save_resume_version(label="V3", content="v3", parent_id=v2.id)

    result = list_resume_versions()

    assert result.success is True
    assert [v.id for v in result.versions] == [v3.id, v2.id, base.id]


def test_list_returns_summaries_without_resume_text(tmp_path, monkeypatch):
    """Listing must not ship resume text — that is what get_resume_version is for.

    A resume is a whole document. Returning content for every version would
    push N full resumes into the model context just to answer "which versions
    do I have?", and limit is optional so the default call is the unbounded one.
    """
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import (
        list_resume_versions,
        save_resume_version,
        get_resume_version,
    )

    body = "SENTINEL RESUME BODY " * 50
    saved = save_resume_version(label="Base", content=body, parent_id=None)

    listed = list_resume_versions()

    assert listed.success is True
    assert not hasattr(listed.versions[0], "content")
    assert listed.versions[0].id == saved.id
    assert listed.versions[0].label == "Base"
    # ...and the text is still reachable one call away
    assert get_resume_version(id=saved.id).version.content == body


def test_save_invalid_input_is_not_reported_as_write_error(tmp_path, monkeypatch):
    """A bad argument type must not tell the caller the disk write failed."""
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import save_resume_version

    result = save_resume_version(label=None, content="x", parent_id=None)

    assert result.success is False
    assert result.error == "invalid_input"


# ---------------------------------------------------------------------------
# Bonus coverage: list_resume_versions filters (mirrors list_jobs conventions)
# ---------------------------------------------------------------------------


def test_list_resume_versions_filters_by_job_url(tmp_path, monkeypatch):
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import list_resume_versions, save_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    tailored = save_resume_version(
        label="Tailored",
        content="tailored",
        parent_id=base.id,
        job_url="https://example.com/job/1",
    )

    result = list_resume_versions(job_url="https://example.com/job/1")

    assert result.success is True
    assert result.count == 1
    assert result.versions[0].id == tailored.id


def test_list_resume_versions_limit(tmp_path, monkeypatch):
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import list_resume_versions, save_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    save_resume_version(label="V2", content="v2", parent_id=base.id)

    result = list_resume_versions(limit=1)

    assert result.success is True
    assert result.count == 1


def test_list_resume_versions_invalid_limit_returns_error(tmp_path, monkeypatch):
    _patch_resumes_path(monkeypatch, tmp_path)
    from tools.resumes import list_resume_versions, save_resume_version

    save_resume_version(label="Base", content="base", parent_id=None)

    result = list_resume_versions(limit=0)

    assert result.success is False
    assert result.error_message is not None


# ---------------------------------------------------------------------------
# _read_resumes / _write_resumes persistence helper tests
# ---------------------------------------------------------------------------


def test_read_resumes_missing_file_returns_empty_store(tmp_path):
    from tools.resumes import _read_resumes

    result = _read_resumes(path=tmp_path / "resumes.json")

    assert result.versions == []


def test_write_resumes_uses_atomic_write_json(tmp_path, monkeypatch):
    """_write_resumes must delegate to tools._storage.atomic_write_json rather
    than hand-rolling the temp-file dance."""
    import tools._storage as storage_mod
    from tools.resumes import ResumeStore, _write_resumes

    calls = []
    original = storage_mod.atomic_write_json

    def _spy(payload, path, *, tmp_prefix):
        calls.append((payload, path, tmp_prefix))
        return original(payload, path, tmp_prefix=tmp_prefix)

    monkeypatch.setattr(storage_mod, "atomic_write_json", _spy)
    # tools.resumes imports `_storage` as a module and calls
    # `_storage.atomic_write_json(...)`, so patching the storage module
    # attribute (not a rebound name in tools.resumes) is what the real code
    # path resolves at call time.

    store = ResumeStore(versions=[])
    dest = tmp_path / "resumes.json"
    _write_resumes(store, path=dest)

    assert len(calls) == 1
    assert calls[0][1] == dest
    assert calls[0][2] == ".resumes_tmp_"
    assert dest.exists()


def test_read_resumes_corrupt_json_raises_value_error(tmp_path):
    from tools.resumes import _read_resumes

    corrupt_path = tmp_path / "resumes.json"
    corrupt_path.write_text("not{json", encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)corrupt"):
        _read_resumes(path=corrupt_path)


def test_read_resumes_non_dict_payload_reports_corrupt(tmp_path):
    from tools.resumes import _read_resumes

    payload_path = tmp_path / "resumes.json"
    payload_path.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)corrupt"):
        _read_resumes(path=payload_path)


def test_read_resumes_unreadable_directory_wraps_as_value_error(tmp_path, monkeypatch):
    """An OSError on read (e.g. path is a directory) must be wrapped as
    ValueError mentioning 'unreadable', not 'corrupt' — the store may be
    perfectly valid, it's just inaccessible."""
    resumes_path = _patch_resumes_path(monkeypatch, tmp_path)
    resumes_path.mkdir()
    from tools.resumes import get_resume_version, list_resume_versions

    listed = list_resume_versions()
    assert listed.success is False
    assert "unreadable" in listed.error_message.lower()

    fetched = get_resume_version(id="anything")
    assert fetched.success is False
    assert fetched.error == "corrupt"
    assert "unreadable" in fetched.message.lower()


def test_write_resumes_creates_parent_dirs(tmp_path):
    from tools.resumes import ResumeStore, _write_resumes

    deep_path = tmp_path / "a" / "b" / "c" / "resumes.json"
    store = ResumeStore(versions=[])

    _write_resumes(store, path=deep_path)

    assert deep_path.exists()


# ---------------------------------------------------------------------------
# save_resume_version failure envelopes
# ---------------------------------------------------------------------------


def test_save_resume_version_corrupt_store_returns_error(tmp_path, monkeypatch):
    resumes_path = _patch_resumes_path(monkeypatch, tmp_path)
    resumes_path.write_text("not{json", encoding="utf-8")
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="text", parent_id=None)

    assert result.success is False
    assert result.error == "corrupt"
    # Read-before-write failure must not touch the corrupt file.
    assert resumes_path.read_text(encoding="utf-8") == "not{json"


def test_save_resume_version_write_error_returns_write_error_code(
    tmp_path, monkeypatch
):
    _patch_resumes_path(monkeypatch, tmp_path)
    import tools.resumes as resumes_mod

    def _boom(store, path=None):
        raise OSError("disk full")

    monkeypatch.setattr(resumes_mod, "_write_resumes", _boom)

    result = resumes_mod.save_resume_version(
        label="Base", content="text", parent_id=None
    )

    assert result.success is False
    assert result.error == "write_error"


def test_get_resume_version_corrupt_store_returns_error(tmp_path, monkeypatch):
    resumes_path = _patch_resumes_path(monkeypatch, tmp_path)
    resumes_path.write_text("not{json", encoding="utf-8")
    from tools.resumes import get_resume_version

    result = get_resume_version(id="anything")

    assert result.success is False
    assert result.error == "corrupt"


def test_list_resume_versions_corrupt_store_returns_error(tmp_path, monkeypatch):
    resumes_path = _patch_resumes_path(monkeypatch, tmp_path)
    resumes_path.write_text("not{json", encoding="utf-8")
    from tools.resumes import list_resume_versions

    result = list_resume_versions()

    assert result.success is False
    assert re.search("(?i)corrupt", result.error_message)


# ---------------------------------------------------------------------------
# _general_resume selection rules (design D6)
# ---------------------------------------------------------------------------


def _v(vid, *, parent_id=None, job_url=None, created_at="2025-01-01T00:00:00+00:00"):
    """Build a ResumeVersion directly, bypassing save_resume_version's rules.

    These are unit tests of the selection rule, so stores are constructed in
    shapes the public save path would not necessarily produce.
    """
    from tools.resumes import ResumeVersion

    return ResumeVersion(
        id=vid,
        label=vid,
        content=f"content-{vid}",
        parent_id=parent_id,
        job_url=job_url,
        created_at=created_at,
    )


def test_general_resume_prefers_newest_untailored_over_the_root():
    """A refreshed general resume must win over the original base.

    This is the rule the whole design rests on: "general" means job_url is
    None, NOT "the root". Returning the root would silently pin analyze_job
    to the user's first-ever upload and ignore every later update.
    """
    from tools.resumes import ResumeStore, _general_resume

    base = _v("base", created_at="2025-01-01T00:00:00+00:00")
    refresh = _v("refresh", parent_id="base", created_at="2025-06-01T00:00:00+00:00")
    store = ResumeStore(versions=[base, refresh])

    assert _general_resume(store).id == "refresh"


def test_general_resume_ignores_job_tailored_versions():
    """A newer version tailored to some job is not the general resume."""
    from tools.resumes import ResumeStore, _general_resume

    base = _v("base", created_at="2025-01-01T00:00:00+00:00")
    tailored = _v(
        "tailored",
        parent_id="base",
        job_url="https://acme.com/job/1",
        created_at="2025-06-01T00:00:00+00:00",
    )
    store = ResumeStore(versions=[base, tailored])

    assert _general_resume(store).id == "base"


def test_general_resume_breaks_multi_root_ties_by_recency():
    """Independent trees: the most recent candidate wins, whichever tree."""
    from tools.resumes import ResumeStore, _general_resume

    old_root = _v("old", created_at="2025-01-01T00:00:00+00:00")
    new_root = _v("new", created_at="2025-09-01T00:00:00+00:00")
    store = ResumeStore(versions=[old_root, new_root])

    assert _general_resume(store).id == "new"


def test_general_resume_fallback_refuses_a_resume_tailored_to_this_job():
    """The fallback must not hand back a resume written for the very job.

    save_resume_version lets the FIRST version carry a job_url, so a store can
    contain nothing untailored. Returning it would produce the inflated
    self-scored match the general-resume rule exists to prevent.
    """
    from tools.resumes import ResumeStore, _general_resume

    only = _v("only", job_url="https://acme.com/job/1")
    store = ResumeStore(versions=[only])

    assert _general_resume(store, for_job_url="https://acme.com/job/1") is None
    # ...but it is a fine general stand-in for a DIFFERENT job
    assert _general_resume(store, for_job_url="https://other.com/job/9").id == "only"


def test_general_resume_empty_store_returns_none():
    from tools.resumes import ResumeStore, _general_resume

    assert _general_resume(ResumeStore()) is None

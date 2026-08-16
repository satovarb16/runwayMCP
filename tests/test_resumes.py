"""Tests for tools/resumes.py — SQLite-backed, job_id FK replaces job_url
(design D1, D2, D6). Append-only is now trigger-backed (see test_db.py for
the raw-SQL proof); these tests cover the tool-boundary behavior.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_resume_version_has_job_id_not_job_url():
    from tools.resumes import ResumeVersion

    assert "job_id" in ResumeVersion.model_fields
    assert "job_url" not in ResumeVersion.model_fields


@pytest.mark.contract
def test_resume_version_has_legacy_job_url_field():
    from tools.resumes import ResumeVersion

    assert "legacy_job_url" in ResumeVersion.model_fields


@pytest.mark.contract
def test_resume_version_requires_explicit_parent_id():
    from pydantic import ValidationError

    from tools.resumes import ResumeVersion

    with pytest.raises(ValidationError):
        ResumeVersion(
            id="v1",
            label="Base",
            content="text",
            created_at="2025-01-01T00:00:00+00:00",
        )


# ---------------------------------------------------------------------------
# R6 / SC-17, SC-18: job_id enforced against real jobs
# ---------------------------------------------------------------------------


def test_sc17_linking_to_existing_job_succeeds(db_path):
    from tools.jobs_store import save_job_analysis
    from tools.resumes import save_resume_version

    job = save_job_analysis(
        url="https://ex.com/1", title="SWE", company="Acme", country="US"
    )

    base = save_resume_version(content="base text", label="Base", parent_id=None)
    result = save_resume_version(
        content="tailored", label="Tailored for Acme", parent_id=base.id, job_id=job.id
    )

    assert result.success is True
    from tools.resumes import get_resume_version

    fetched = get_resume_version(id=result.id)
    assert fetched.version.job_id == job.id


def test_sc18_linking_to_nonexistent_job_rejected(db_path):
    from tools.resumes import save_resume_version

    base = save_resume_version(content="base text", label="Base", parent_id=None)

    result = save_resume_version(
        content="tailored", label="Tailored", parent_id=base.id, job_id="ghost"
    )

    assert result.success is False
    assert result.error == "job_not_found"
    assert "save_job_analysis" in (result.message or "")

    from tools.resumes import list_resume_versions

    assert list_resume_versions().count == 1  # nothing persisted


# ---------------------------------------------------------------------------
# R7 / SC-19, SC-20: append-only preserved at the tool boundary
# ---------------------------------------------------------------------------


def test_sc19_saved_content_unchanged_by_later_saves(db_path):
    from tools.resumes import save_resume_version, get_resume_version

    v1 = save_resume_version(content="Version 1 text", label="V1", parent_id=None)
    save_resume_version(content="Version 2", label="V2", parent_id=v1.id)
    save_resume_version(content="Version 3", label="V3", parent_id=v1.id)

    fetched = get_resume_version(id=v1.id)
    assert fetched.version.content == "Version 1 text"


def test_sc20_no_tool_can_overwrite_or_delete(db_path):
    """No public tool in resumes.py mutates an existing version's content."""
    import tools.resumes as resumes_mod

    public_funcs = [
        name
        for name in (
            "save_resume_version",
            "get_resume_version",
            "list_resume_versions",
        )
        if hasattr(resumes_mod, name)
    ]
    assert set(public_funcs) == {
        "save_resume_version",
        "get_resume_version",
        "list_resume_versions",
    }
    # No delete_* function exists anywhere in the module.
    assert not any(n.startswith("delete") for n in dir(resumes_mod))


# ---------------------------------------------------------------------------
# R8 — single-root invariant, ported verbatim (SC-02..SC-05 of obs #333)
# ---------------------------------------------------------------------------


def test_first_save_on_empty_store_establishes_base(db_path):
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="text", parent_id=None)

    assert result.success is True
    assert result.parent_id is None


def test_empty_store_rejects_nonnull_parent_id(db_path):
    from tools.resumes import save_resume_version

    result = save_resume_version(label="Base", content="text", parent_id="some-id")

    assert result.success is False
    assert result.error == "invalid_parent"


def test_nonempty_store_rejects_none_parent_id(db_path):
    from tools.resumes import save_resume_version

    save_resume_version(label="Base", content="text", parent_id=None)
    result = save_resume_version(label="Second base?", content="text", parent_id=None)

    assert result.success is False
    assert result.error == "invalid_parent"


def test_dangling_parent_id_rejected(db_path):
    from tools.resumes import save_resume_version

    base = save_resume_version(label="Base", content="text", parent_id=None)
    result = save_resume_version(label="Child", content="text", parent_id="unknown-id")

    assert result.success is False
    assert result.error == "parent_not_found"

    from tools.resumes import list_resume_versions

    listed = list_resume_versions()
    assert listed.count == 1
    assert listed.versions[0].id == base.id


def test_save_invalid_input_not_reported_as_write_error(db_path):
    from tools.resumes import save_resume_version

    result = save_resume_version(label=None, content="x", parent_id=None)

    assert result.success is False
    assert result.error == "invalid_input"


# ---------------------------------------------------------------------------
# get_resume_version / list_resume_versions
# ---------------------------------------------------------------------------


def test_get_resume_version_by_id(db_path):
    from tools.resumes import save_resume_version, get_resume_version

    base = save_resume_version(label="Base", content="base text", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2 text", parent_id=base.id)

    result = get_resume_version(id=v2.id)

    assert result.success is True
    assert result.version.id == v2.id


def test_get_resume_version_unknown_id_not_found(db_path):
    from tools.resumes import get_resume_version

    result = get_resume_version(id="ghost")

    assert result.success is False
    assert result.error == "not_found"


def test_get_resume_version_latest(db_path):
    from tools.resumes import save_resume_version, get_resume_version

    base = save_resume_version(label="Base", content="base", parent_id=None)
    v2 = save_resume_version(label="V2", content="v2", parent_id=base.id)

    result = get_resume_version(id="latest")

    assert result.success is True
    assert result.version.id == v2.id


def test_list_resume_versions_newest_first_and_no_content_field(db_path):
    from tools.resumes import save_resume_version, list_resume_versions

    base = save_resume_version(label="Base", content="base" * 100, parent_id=None)
    v2 = save_resume_version(label="V2", content="v2", parent_id=base.id)

    result = list_resume_versions()

    assert [v.id for v in result.versions] == [v2.id, base.id]
    assert not hasattr(result.versions[0], "content")


def test_list_resume_versions_filters_by_job_id(db_path):
    from tools.jobs_store import save_job_analysis
    from tools.resumes import save_resume_version, list_resume_versions

    job = save_job_analysis(
        url="https://ex.com/1", title="SWE", company="Acme", country="US"
    )
    base = save_resume_version(label="Base", content="base", parent_id=None)
    tailored = save_resume_version(
        label="Tailored", content="tailored", parent_id=base.id, job_id=job.id
    )

    result = list_resume_versions(job_id=job.id)

    assert result.count == 1
    assert result.versions[0].id == tailored.id


def test_list_resume_versions_limit_and_invalid_limit(db_path):
    from tools.resumes import save_resume_version, list_resume_versions

    save_resume_version(label="Base", content="base", parent_id=None)

    assert list_resume_versions(limit=1).count == 1
    assert list_resume_versions(limit=0).success is False


def test_missing_database_is_normal_not_error(db_path):
    from tools.resumes import list_resume_versions

    result = list_resume_versions()

    assert result.success is True
    assert result.count == 0


# ---------------------------------------------------------------------------
# R9 / SC-21 — the anti-inflation guard, now keyed by job_id
# ---------------------------------------------------------------------------


def _mk_version(
    vid,
    *,
    parent_id=None,
    job_id=None,
    legacy_job_url=None,
    created_at="2025-01-01T00:00:00+00:00",
):
    from tools.resumes import ResumeVersion

    return ResumeVersion(
        id=vid,
        label=vid,
        content=f"content-{vid}",
        parent_id=parent_id,
        job_id=job_id,
        legacy_job_url=legacy_job_url,
        created_at=created_at,
    )


def test_sc21_general_resume_selection_excludes_job_tailored(db_path):
    from tools.jobs_store import save_job_analysis
    from tools.resumes import save_resume_version, _general_resume

    job = save_job_analysis(
        url="https://ex.com/1", title="SWE", company="Acme", country="US"
    )
    base = save_resume_version(label="General", content="general", parent_id=None)
    save_resume_version(
        label="Tailored for J1",
        content="tailored",
        parent_id=base.id,
        job_id=job.id,
    )

    selected = _general_resume(job_id=job.id)

    assert selected is not None
    assert selected.id == base.id


def test_general_resume_excludes_migration_orphan(db_path):
    """The migration-orphan (legacy_job_url set) must never be selected as
    the general resume, even when it is the most recent row with
    job_id IS NULL."""
    from tools._db import connect
    from tools.resumes import _general_resume

    with connect(db_path, write=True) as conn:
        conn.execute(
            "INSERT INTO resume_versions (id, label, content, parent_id, "
            "job_id, legacy_job_url, created_at) VALUES "
            "('general-old', 'General', 'text', NULL, NULL, NULL, "
            "'2025-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO resume_versions (id, label, content, parent_id, "
            "job_id, legacy_job_url, created_at) VALUES "
            "('orphan-newest', 'Orphan', 'text', 'general-old', NULL, "
            "'https://gone.com/x', '2025-12-01T00:00:00+00:00')"
        )

    selected = _general_resume()

    assert selected is not None
    assert selected.id == "general-old"


def test_general_resume_empty_store_returns_none(db_path):
    from tools.resumes import _general_resume

    assert _general_resume() is None


# ---------------------------------------------------------------------------
# Finding 5 (PR2 apply-fix review): _general_resume's FALLBACK branches
# (reached only when no job_id IS NULL AND legacy_job_url IS NULL row
# exists) must also exclude migration orphans. Both existing guard tests
# above only ever exercise the primary query, which already had the
# legacy_job_url IS NULL clause — these target the fallback specifically by
# leaving a lone orphan as the only candidate "root".
# ---------------------------------------------------------------------------


def test_general_resume_fallback_excludes_orphan_when_job_id_none(db_path):
    from tools._db import connect
    from tools.resumes import _general_resume

    with connect(db_path, write=True) as conn:
        conn.execute(
            "INSERT INTO resume_versions (id, label, content, parent_id, "
            "job_id, legacy_job_url, created_at) VALUES "
            "('orphan-root', 'Orphan', 'text', NULL, NULL, "
            "'https://gone.com/x', '2025-06-01T00:00:00+00:00')"
        )

    # No general candidate and no job_id given: the fallback's only
    # candidate root is the orphan. Promoting it would make it the scoring
    # baseline for every future job — must return None instead.
    assert _general_resume(job_id=None) is None


def test_general_resume_fallback_excludes_orphan_when_job_id_given(db_path):
    from tools._db import connect
    from tools.jobs_store import save_job_analysis
    from tools.resumes import _general_resume

    job = save_job_analysis(
        url="https://ex.com/2", title="T", company="C", country="US"
    )

    with connect(db_path, write=True) as conn:
        conn.execute(
            "INSERT INTO resume_versions (id, label, content, parent_id, "
            "job_id, legacy_job_url, created_at) VALUES "
            "('orphan-root', 'Orphan', 'text', NULL, NULL, "
            "'https://gone.com/x', '2025-06-01T00:00:00+00:00')"
        )

    assert _general_resume(job_id=job.id) is None

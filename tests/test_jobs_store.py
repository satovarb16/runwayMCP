"""Tests for tools/jobs_store.py — SQLite-backed job store (design D1, D10).

get_job and list_jobs's company filter are PR3a's, not tested here. Covers
save_job_analysis (SC-01..SC-10, SC-14 write-half), list_jobs (unchanged
contract, now SQL-backed), set_application_status (+id), and the PR#32
omitted-argument regression — flagged by design as "the single most likely
place for a silently-passing-but-wrong test in this release", so its fixture
carries non-default prior values throughout (task 2.4j).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_stored_job_has_no_visa_verdict_field():
    from tools.jobs_store import StoredJob

    assert "visa_verdict" not in StoredJob.model_fields


@pytest.mark.contract
def test_stored_job_has_id_custom_title_country():
    from tools.jobs_store import StoredJob

    job = StoredJob(
        id="J1",
        url="https://ex.com/1",
        custom_title=None,
        title="SWE",
        company="Acme",
        country="USA",
        status="not_applied",
        analyzed_at="2025-01-01T00:00:00+00:00",
    )
    assert job.id == "J1"
    assert job.country == "USA"


@pytest.mark.contract
def test_stored_job_rejects_unknown_fields():
    from pydantic import ValidationError

    from tools.jobs_store import StoredJob

    with pytest.raises(ValidationError):
        StoredJob(
            id="J1",
            url="https://ex.com/1",
            title="SWE",
            company="Acme",
            country="USA",
            analyzed_at="2025-01-01T00:00:00+00:00",
            visa_verdict="GREEN",
        )


# ---------------------------------------------------------------------------
# R1 / SC-01..SC-03: at least one handle required
# ---------------------------------------------------------------------------


def test_sc01_creation_with_url_only(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        url="https://example.com/job/1", title="SWE", company="Acme", country="USA"
    )

    assert result.success is True
    assert result.id is not None
    assert result.url == "https://example.com/job/1"
    assert result.custom_title is None


def test_sc02_creation_with_custom_title_only_carries_remember_message(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        custom_title="Acme referral role", title="SWE", company="Acme", country="USA"
    )

    assert result.success is True
    assert result.id is not None
    assert result.url is None
    assert result.custom_title == "Acme referral role"
    assert result.message is not None
    assert "custom" in result.message.lower() or "title" in result.message.lower()


def test_sc03_neither_url_nor_custom_title_rejected(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(title="SWE", company="Acme", country="USA")

    assert result.success is False
    assert result.error == "invalid_input"

    from tools.jobs_store import list_jobs

    assert list_jobs().count == 0


# ---------------------------------------------------------------------------
# R2 / SC-04..SC-05: identity survives a URL supplied later
# ---------------------------------------------------------------------------


def test_sc04_supplying_url_later_preserves_id(db_path):
    from tools.jobs_store import save_job_analysis
    from tools.resumes import save_resume_version

    created = save_job_analysis(
        custom_title="Acme referral role", title="SWE", company="Acme", country="USA"
    )
    job_id = created.id

    save_resume_version(
        content="tailored", label="Tailored", parent_id=None, job_id=job_id
    )

    updated = save_job_analysis(
        id=job_id,
        url="https://example.com/job/1",
        title="SWE",
        company="Acme",
        country="USA",
    )

    assert updated.success is True
    assert updated.id == job_id
    assert updated.url == "https://example.com/job/1"
    assert updated.custom_title == "Acme referral role"  # preserved, omitted this call


def test_sc05_unknown_id_on_explicit_update_rejected(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        id="ghost", url="https://ex.com/x", title="X", company="Y", country="Z"
    )

    assert result.success is False
    assert result.error == "not_found"

    from tools.jobs_store import list_jobs

    assert list_jobs().count == 0


# ---------------------------------------------------------------------------
# R3 / SC-06..SC-09: upsert semantics
# ---------------------------------------------------------------------------


def test_sc06_resave_same_url_updates_existing_id_unchanged(db_path):
    from tools.jobs_store import save_job_analysis

    first = save_job_analysis(
        url="https://example.com/job/1", title="SWE", company="Acme", country="USA"
    )
    second = save_job_analysis(
        url="https://example.com/job/1",
        title="SWE",
        company="Acme",
        country="USA",
        score=80,
    )

    assert second.success is True
    assert second.id == first.id
    assert second.updated is True

    from tools.jobs_store import list_jobs

    assert list_jobs().count == 1


def test_sc07_new_url_creates_new_record(db_path):
    from tools.jobs_store import save_job_analysis

    first = save_job_analysis(
        url="https://example.com/job/1", title="SWE", company="Acme", country="USA"
    )
    second = save_job_analysis(
        url="https://example.com/job/2", title="Other", company="Beta", country="USA"
    )

    assert second.success is True
    assert second.id != first.id

    from tools.jobs_store import list_jobs

    assert list_jobs().count == 2


def test_sc08_resave_same_custom_title_never_matches(db_path):
    from tools.jobs_store import save_job_analysis

    first = save_job_analysis(
        custom_title="Referral role", title="SWE", company="Acme", country="USA"
    )
    second = save_job_analysis(
        custom_title="Referral role", title="SWE", company="Acme", country="USA"
    )

    assert second.success is True
    assert second.id != first.id

    from tools.jobs_store import list_jobs

    assert list_jobs().count == 2


def test_sc09_setting_url_to_one_owned_by_another_job_rejected(db_path):
    from tools.jobs_store import save_job_analysis

    job_a = save_job_analysis(
        url="https://example.com/job/1", title="X", company="Y", country="Z"
    )
    job_b = save_job_analysis(custom_title="B", title="X", company="Y", country="Z")

    result = save_job_analysis(
        id=job_b.id,
        url="https://example.com/job/1",
        title="X",
        company="Y",
        country="Z",
    )

    assert result.success is False
    assert result.error == "duplicate_url"

    from tools.jobs_store import list_jobs

    jobs = {j.id: j for j in list_jobs().jobs}
    assert jobs[job_b.id].url is None  # unchanged
    assert jobs[job_a.id].url == "https://example.com/job/1"  # unchanged


# ---------------------------------------------------------------------------
# SC-10 — highest-risk silently-passing test: prior values are non-default
# ---------------------------------------------------------------------------


def test_sc10_omitted_optional_fields_preserve_prior_nondefault_values(db_path):
    """The fixture's prior notes/score/recommendation/status are all
    non-default, so this test fails under BOTH a correct partial UPDATE and
    (crucially) is the only shape that would also fail under an incorrect
    full overwrite with defaults — a fixture with all-None priors passes
    under both implementations and proves nothing (design §14)."""
    from tools.jobs_store import save_job_analysis, set_application_status

    created = save_job_analysis(
        url="https://example.com/job/1",
        title="SWE",
        company="Acme",
        country="USA",
        score=70,
        recommendation="CONSIDER",
        notes="keep me",
    )
    set_application_status(id=created.id, status="interviewing")

    result = save_job_analysis(
        id=created.id,
        url=None,
        title="SWE",
        company="Acme",
        country="USA",
        score=85,  # explicit — must overwrite
    )

    assert result.success is True

    from tools.jobs_store import list_jobs

    record = list_jobs().jobs[0]
    assert record.score == 85  # explicit arg wins
    assert record.recommendation == "CONSIDER"  # omitted — preserved
    assert record.notes == "keep me"  # omitted — preserved
    assert record.status == "interviewing"  # never touched by save_job_analysis


# ---------------------------------------------------------------------------
# SC-14 (write-half): jd_text optional, round-trips through job_descriptions
# ---------------------------------------------------------------------------


def test_sc14_jd_text_omitted_on_save_leaves_no_job_descriptions_row(db_path):
    from tools.jobs_store import save_job_analysis
    from tools._db import connect

    result = save_job_analysis(
        url="https://example.com/1", title="SWE", company="Acme", country="USA"
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM job_descriptions WHERE job_id = ?", (result.id,)
        ).fetchone()
    assert row is None


def test_jd_text_provided_writes_job_descriptions_row(db_path):
    from tools.jobs_store import save_job_analysis
    from tools._db import connect

    result = save_job_analysis(
        url="https://example.com/1",
        title="SWE",
        company="Acme",
        country="USA",
        jd_text="Full posting...\nwith newlines",
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT jd_text FROM job_descriptions WHERE job_id = ?", (result.id,)
        ).fetchone()
    assert row["jd_text"] == "Full posting...\nwith newlines"


def test_jd_text_omitted_on_update_leaves_existing_jd_text_unchanged(db_path):
    from tools.jobs_store import save_job_analysis
    from tools._db import connect

    created = save_job_analysis(
        url="https://example.com/1",
        title="SWE",
        company="Acme",
        country="USA",
        jd_text="original JD",
    )
    save_job_analysis(
        id=created.id, url=None, title="SWE", company="Acme", country="USA"
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT jd_text FROM job_descriptions WHERE job_id = ?", (created.id,)
        ).fetchone()
    assert row["jd_text"] == "original JD"


# ---------------------------------------------------------------------------
# list_jobs — unchanged contract, now SQL-backed
# ---------------------------------------------------------------------------


def test_list_jobs_empty_store_returns_empty(db_path):
    from tools.jobs_store import list_jobs

    result = list_jobs()

    assert result.success is True
    assert result.jobs == []
    assert result.count == 0


def test_list_jobs_status_filter_and_min_score(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status, list_jobs

    a = save_job_analysis(
        url="https://a.com", title="A", company="A", country="US", score=90
    )
    set_application_status(id=a.id, status="applied")
    save_job_analysis(
        url="https://b.com", title="B", company="B", country="US", score=50
    )

    result = list_jobs(status="applied", min_score=60)

    assert result.success is True
    assert result.count == 1
    assert result.jobs[0].url == "https://a.com"


def test_list_jobs_status_list_and_invalid_member(db_path):
    from tools.jobs_store import save_job_analysis, list_jobs

    save_job_analysis(url="https://a.com", title="A", company="A", country="US")

    result = list_jobs(status=["applied", "bogus"])

    assert result.success is False
    assert "bogus" in result.error_message


def test_list_jobs_sort_by_score_desc_none_last(db_path):
    from tools.jobs_store import save_job_analysis, list_jobs

    save_job_analysis(
        url="https://a.com", title="A", company="A", country="US", score=70
    )
    save_job_analysis(
        url="https://b.com", title="B", company="B", country="US", score=90
    )
    save_job_analysis(url="https://c.com", title="C", company="C", country="US")

    result = list_jobs(sort_by="score")

    assert [j.url for j in result.jobs] == [
        "https://b.com",
        "https://a.com",
        "https://c.com",
    ]


def test_list_jobs_limit_and_default_sort_newest_first(db_path):
    from tools.jobs_store import save_job_analysis, list_jobs

    save_job_analysis(url="https://a.com", title="A", company="A", country="US")
    save_job_analysis(url="https://b.com", title="B", company="B", country="US")

    result = list_jobs()

    assert result.jobs[0].url == "https://b.com"


def test_list_jobs_invalid_sort_by_returns_error(db_path):
    from tools.jobs_store import list_jobs

    result = list_jobs(sort_by="bogus_field")

    assert result.success is False
    assert "bogus_field" in result.error_message


def test_list_jobs_limit_zero_or_negative_returns_error(db_path):
    from tools.jobs_store import list_jobs

    assert list_jobs(limit=0).success is False
    assert list_jobs(limit=-1).success is False


def test_list_jobs_since_filter(db_path):
    from tools.jobs_store import save_job_analysis, list_jobs

    # Stamp analyzed_at deterministically by monkeypatching is overkill here;
    # instead rely on two saves in sequence and filter with a cutoff before
    # both, then after both.
    save_job_analysis(url="https://a.com", title="A", company="A", country="US")
    result_before = list_jobs(since="2000-01-01T00:00:00Z")
    result_after = list_jobs(since="2999-01-01T00:00:00Z")

    assert result_before.count == 1
    assert result_after.count == 0


def test_list_jobs_since_filter_normalizes_offset_to_utc(db_path):
    """Finding 4 (PR2 apply-fix review): `since` must be UTC-normalized
    before the lexicographic string comparison against analyzed_at, which is
    always stored '+00:00'-stamped. A `since` cutoff expressed with a
    negative offset that is numerically AFTER a stored UTC timestamp must
    exclude that record — comparing the raw un-normalized strings would
    include it, because '09' < '10' even though 09:00-05:00 (14:00 UTC) is
    LATER than 10:00+00:00."""
    from tools._db import connect
    from tools.jobs_store import list_jobs

    with connect(db_path, write=True) as conn:
        conn.execute(
            "INSERT INTO jobs (id, url, custom_title, title, company, country, "
            "status, score, recommendation, notes, analyzed_at) VALUES "
            "('J1', 'https://ex.com/1', NULL, 'T', 'C', NULL, 'not_applied', "
            "NULL, NULL, NULL, '2025-06-01T10:00:00+00:00')"
        )

    result = list_jobs(since="2025-06-01T09:00:00-05:00")

    assert result.success is True
    assert result.count == 0


def test_list_jobs_never_returns_jd_text_field(db_path):
    """SC-15 (jobs half): the summary shape has no jd_text field at all."""
    from tools.jobs_store import save_job_analysis, list_jobs, StoredJob

    save_job_analysis(
        url="https://a.com",
        title="A",
        company="A",
        country="US",
        jd_text="huge posting" * 500,
    )

    assert "jd_text" not in StoredJob.model_fields
    result = list_jobs()
    assert result.jobs[0].url == "https://a.com"


# ---------------------------------------------------------------------------
# set_application_status — now accepts id OR url
# ---------------------------------------------------------------------------


def test_set_application_status_by_id(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status, list_jobs

    created = save_job_analysis(
        url="https://a.com", title="A", company="A", country="US", notes="keep me"
    )

    result = set_application_status(id=created.id, status="applied")

    assert result.success is True
    record = list_jobs().jobs[0]
    assert record.status == "applied"
    assert record.notes == "keep me"


def test_set_application_status_by_url(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status

    save_job_analysis(url="https://a.com", title="A", company="A", country="US")

    result = set_application_status(url="https://a.com", status="applied")

    assert result.success is True
    assert result.status == "applied"


def test_set_application_status_invalid_status_rejected(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status

    created = save_job_analysis(
        url="https://a.com", title="A", company="A", country="US"
    )

    result = set_application_status(id=created.id, status="bogus")

    assert result.success is False
    assert result.error == "invalid_status"


def test_set_application_status_unknown_id_returns_not_found(db_path):
    from tools.jobs_store import set_application_status

    result = set_application_status(id="ghost", status="applied")

    assert result.success is False
    assert result.error == "not_found"


def test_set_application_status_free_transition_any_to_any(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status

    created = save_job_analysis(
        url="https://a.com", title="A", company="A", country="US"
    )
    set_application_status(id=created.id, status="rejected")

    result = set_application_status(id=created.id, status="interviewing")

    assert result.success is True
    assert result.status == "interviewing"


# ---------------------------------------------------------------------------
# Corruption / error-envelope discipline
# ---------------------------------------------------------------------------


def test_list_jobs_on_missing_database_is_normal_not_error(db_path):
    from tools.jobs_store import list_jobs

    result = list_jobs()

    assert result.success is True
    assert result.count == 0


def test_save_job_analysis_never_raises_on_bad_score_type(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        url="https://a.com",
        title="A",
        company="A",
        country="US",
        score="not-a-valid-int-string",  # type: ignore[arg-type]
    )

    assert result.success is False


# ---------------------------------------------------------------------------
# ApplicationStatus enum — unchanged 7 values
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_application_status_has_exactly_seven_values():
    from tools.jobs_store import ApplicationStatus

    values = {member.value for member in ApplicationStatus}
    assert values == {
        "not_applied",
        "applied",
        "interviewing",
        "offer",
        "rejected",
        "withdrawn",
        "ghosted",
    }


# ---------------------------------------------------------------------------
# D10 advisory: possible_duplicate_id on a URL-less insert matching an
# existing job's (company, title) — non-blocking, per design's "the user has
# the id to reconcile them" reasoning. Not gated by an explicit spec SC, but
# implemented per design D10 and tested here rather than left unverified.
# ---------------------------------------------------------------------------


def test_url_less_insert_matching_company_and_title_flags_possible_duplicate(db_path):
    from tools.jobs_store import save_job_analysis

    first = save_job_analysis(
        url="https://example.com/job/1", title="SWE", company="Acme", country="USA"
    )

    result = save_job_analysis(
        custom_title="Referral for the same role",
        title="SWE",
        company="Acme",
        country="USA",
    )

    assert result.success is True  # advisory only — never blocks
    assert result.possible_duplicate_id == first.id


def test_url_less_insert_with_no_match_has_no_possible_duplicate(db_path):
    from tools.jobs_store import save_job_analysis

    result = save_job_analysis(
        custom_title="Unique referral", title="SWE", company="Acme", country="USA"
    )

    assert result.success is True
    assert result.possible_duplicate_id is None


# ---------------------------------------------------------------------------
# 2.6f: extra="forbid" still bites — an undeclared table column raises at
# first read rather than being silently dropped.
# ---------------------------------------------------------------------------


def test_undeclared_column_on_jobs_raises_at_first_read(db_path):
    from tools._db import connect
    from tools.jobs_store import list_jobs

    with connect(db_path, write=True) as conn:
        conn.execute("ALTER TABLE jobs ADD COLUMN unexpected_future_column TEXT")
        conn.execute(
            "INSERT INTO jobs (id, url, custom_title, title, company, country, "
            "status, score, recommendation, notes, analyzed_at, "
            "unexpected_future_column) VALUES "
            "('J1', 'https://ex.com/1', NULL, 'SWE', 'Acme', 'USA', "
            "'not_applied', NULL, NULL, NULL, '2025-01-01T00:00:00+00:00', "
            "'surprise')"
        )

    result = list_jobs()

    assert result.success is False
    assert result.error_message is not None


# ---------------------------------------------------------------------------
# SC-22 (partial — full round-trip needs get_job, deferred to PR3a):
# list_jobs -> list_resume_versions(job_id=...) -> get_resume_version chain.
# ---------------------------------------------------------------------------


def test_sc22_partial_cross_conversation_recall_without_get_job(db_path):
    from tools.jobs_store import save_job_analysis, set_application_status, list_jobs
    from tools.resumes import (
        save_resume_version,
        list_resume_versions,
        get_resume_version,
    )

    job = save_job_analysis(
        url="https://example.com/datadog", title="SWE", company="Datadog", country="US"
    )
    set_application_status(id=job.id, status="interviewing")
    version = save_resume_version(
        content="Datadog-tailored resume text",
        label="Datadog — infra focus",
        parent_id=None,
        job_id=job.id,
    )

    found_jobs = list_jobs(status="interviewing")
    assert found_jobs.count == 1
    assert found_jobs.jobs[0].id == job.id

    linked_versions = list_resume_versions(job_id=job.id)
    assert linked_versions.count == 1
    assert linked_versions.versions[0].id == version.id
    assert linked_versions.versions[0].label == "Datadog — infra focus"

    fetched = get_resume_version(id=version.id)
    assert fetched.version.content == "Datadog-tailored resume text"

"""Tests for Pydantic models in tools.profile (Phase 1 — TDD RED)."""

from __future__ import annotations



# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------


def test_experience_required_fields():
    from tools.profile import ExperienceEntry

    e = ExperienceEntry(company="Acme", title="Engineer")
    assert e.company == "Acme"
    assert e.title == "Engineer"
    assert e.duration_years is None
    assert e.description is None


def test_experience_all_fields():
    from tools.profile import ExperienceEntry

    e = ExperienceEntry(
        company="BigCo",
        title="Senior Engineer",
        duration_years=3.5,
        description="Led platform team.",
    )
    assert e.duration_years == 3.5
    assert e.description == "Led platform team."


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------


def test_education_required_fields():
    from tools.profile import EducationEntry

    ed = EducationEntry(institution="MIT")
    assert ed.institution == "MIT"
    assert ed.degree is None
    assert ed.field is None
    assert ed.year is None


def test_education_all_fields():
    from tools.profile import EducationEntry

    ed = EducationEntry(institution="MIT", degree="BSc", field="CS", year=2018)
    assert ed.degree == "BSc"
    assert ed.year == 2018


# ---------------------------------------------------------------------------
# ProfileData
# ---------------------------------------------------------------------------


def test_profile_data_accepts_empty_lists():
    from tools.profile import ProfileData

    p = ProfileData(name=None)
    assert p.skills == []
    assert p.experience == []
    assert p.education == []
    assert p.languages == []
    assert p.summary == ""


def test_profile_data_full():
    from tools.profile import ExperienceEntry, EducationEntry, ProfileData

    p = ProfileData(
        name="Jane Doe",
        email="jane@example.com",
        location="Buenos Aires",
        skills=["Python", "Go"],
        experience=[ExperienceEntry(company="Acme", title="SWE", duration_years=2.0)],
        education=[EducationEntry(institution="UBA", degree="BSc", field="CS", year=2020)],
        languages=["English", "Spanish"],
        summary="Experienced engineer.",
    )
    assert p.name == "Jane Doe"
    assert len(p.skills) == 2
    assert p.experience[0].company == "Acme"
    assert p.education[0].institution == "UBA"


# ---------------------------------------------------------------------------
# ProfileSetupResult — success shape
# ---------------------------------------------------------------------------


def test_profile_setup_result_success_shape():
    from tools.profile import ProfileSetupResult

    result = ProfileSetupResult(
        success=True,
        storage_path="/home/user/.config/runway-mcp/profile.json",
        profile_summary={"name": "Jane Doe", "skills_count": 1, "experience_years": 0.0},
    )
    assert result.success is True
    assert result.storage_path is not None
    assert result.profile_summary is not None
    assert result.error_message is None


def test_profile_setup_result_failure_shape():
    from tools.profile import ProfileSetupResult

    result = ProfileSetupResult(
        success=False,
        error_message="File not found.",
    )
    assert result.success is False
    assert result.error_message == "File not found."
    assert result.profile_summary is None
    assert result.storage_path is None


def test_profile_setup_result_success_requires_no_error_message():
    from tools.profile import ProfileSetupResult

    result = ProfileSetupResult(
        success=True,
        storage_path="/some/path.json",
        profile_summary={"name": "X", "skills_count": 0, "experience_years": 0.0},
        error_message=None,
    )
    assert result.error_message is None

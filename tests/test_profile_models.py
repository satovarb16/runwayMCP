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
        education=[
            EducationEntry(institution="UBA", degree="BSc", field="CS", year=2020)
        ],
        languages=["English", "Spanish"],
        summary="Experienced engineer.",
    )
    assert p.name == "Jane Doe"
    assert len(p.skills) == 2
    assert p.experience[0].company == "Acme"
    assert p.education[0].institution == "UBA"

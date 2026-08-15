"""Profile management: read-only legacy migration hatch.

DEPRECATED — this module will be removed in 0.3.0. ``setup_profile`` and
``update_profile`` were removed in this change: resumes are now
version-tracked via ``tools.resumes`` (``save_resume_version``,
``get_resume_version``, ``list_resume_versions``), and the server never
generates or tailors resume content — that stays Claude's job.

``get_profile`` is kept, read-only, for one release so Claude can still read a
legacy profile and re-submit it as text via ``save_resume_version``. The server
never writes to or deletes ``profile.json``: once you have migrated, delete
``~/.config/runway-mcp/profile.json`` yourself. Nothing else reads it.

Profiles are stored at ~/.config/runway-mcp/profile.json.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PROFILE_PATH: Path = Path.home() / ".config" / "runway-mcp" / "profile.json"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExperienceEntry(BaseModel):
    """A single work experience entry extracted from a CV."""

    company: str
    title: str
    duration_years: float | None = None
    description: str | None = None


class EducationEntry(BaseModel):
    """A single education entry extracted from a CV."""

    institution: str
    degree: str | None = None
    field: str | None = None
    year: int | None = None


class ProfileData(BaseModel):
    """Structured profile data extracted from a CV.

    Personal fields (name, email, location) are stored as flat top-level fields
    rather than nested under a 'personal' object — this matches what Claude
    naturally returns and simplifies downstream consumers like analyze_job.
    """

    name: str | None = None
    email: str | None = None
    location: str | None = None
    skills: list[str] = []
    experience: list[ExperienceEntry] = []
    education: list[EducationEntry] = []
    languages: list[str] = []
    summary: str = ""


class GetProfileResult(BaseModel):
    """Return value for get_profile."""

    success: bool
    profile: ProfileData | None = None
    error: str | None = None  # "no_profile" | "corrupt"
    message: str | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


class ProfileNotFound(ValueError):
    """No profile.json on disk.

    A ValueError subclass so any caller with a plain `except ValueError` still
    works, but a distinct type so "missing" and "corrupt" cannot be confused.
    Reporting a missing file as corruption is how a user ends up hunting for
    damage in a file that was simply never there.
    """


def _read_profile(path: Path | None = None) -> ProfileData:
    """Read and parse the stored profile JSON.

    Args:
        path: Path to the profile JSON file. If None, uses the module-level
              _PROFILE_PATH (resolved at call time so tests can monkeypatch it).
              Mirrors _read_jobs/_read_resumes, which take the same parameter.

    Returns:
        ProfileData parsed from the file.

    Raises:
        ProfileNotFound: if the file does not exist.
        ValueError:      if its content is malformed.
    """
    resolved = path if path is not None else _PROFILE_PATH
    if not resolved.exists():
        raise ProfileNotFound("No profile found.")
    try:
        return ProfileData.model_validate_json(resolved.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Profile file is corrupt: {exc}") from exc


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def get_profile() -> GetProfileResult:
    """Return the stored candidate profile, if a legacy one exists.

    Read-only migration hatch: reads ~/.config/runway-mcp/profile.json if
    present. The server never writes or deletes this file. Returns a
    structured error envelope (never raises) when no profile exists yet or
    the stored file is corrupt.

    Returns:
        GetProfileResult with success=True and the profile on success, or
        success=False with error/message on failure.
    """
    # Single read, no exists() pre-check: checking and then reading let the
    # file disappear in between, and the missing file came back labelled
    # "corrupt" with the message "No profile found." — an envelope that
    # contradicts itself. The docstring now tells users to delete this file
    # by hand, so that race is reachable in normal use.
    try:
        profile = _read_profile()
    except ProfileNotFound:
        return GetProfileResult(
            success=False,
            error="no_profile",
            message=(
                "No legacy profile found. Save your resume text with "
                "save_resume_version instead."
            ),
        )
    except ValueError as exc:
        return GetProfileResult(success=False, error="corrupt", message=str(exc))

    return GetProfileResult(success=True, profile=profile)

"""Profile management tools: setup_profile, update_profile, get_profile.

The conversation-side Claude reads the user's CV (it can read .pdf/.docx
natively) and extracts the structured profile. These tools only PERSIST and
RETRIEVE that structured data — they never call back to the model. This keeps
the server free of MCP sampling, which the host (e.g. Claude Code) may not
support, and matches the project's philosophy: tools shape data, Claude reasons.

Profiles are stored at ~/.config/runway-mcp/profile.json.
"""

from __future__ import annotations

import tempfile
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


class ProfileSummary(BaseModel):
    """Lightweight summary included in the tool return value."""

    name: str | None = None
    skills_count: int = 0
    experience_years: float = 0.0


class ProfileSetupResult(BaseModel):
    """Return value for setup_profile and update_profile."""

    success: bool
    profile_summary: dict | None = None
    storage_path: str | None = None
    error_message: str | None = None


class GetProfileResult(BaseModel):
    """Return value for get_profile."""

    success: bool
    profile: ProfileData | None = None
    error: str | None = None  # "no_profile" | "corrupt"
    message: str | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _read_profile(path: Path | None = None) -> ProfileData:
    """Read and parse the stored profile JSON.

    Args:
        path: Path to the profile JSON file. If None, uses the module-level
              _PROFILE_PATH (resolved at call time so tests can monkeypatch it).

    Returns:
        ProfileData parsed from the file.

    Raises:
        ValueError: if the file does not exist or its content is malformed.
    """
    resolved = path if path is not None else _PROFILE_PATH
    if not resolved.exists():
        raise ValueError("No profile found. Run setup_profile first.")
    try:
        return ProfileData.model_validate_json(resolved.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Profile file is corrupt: {exc}") from exc


def load_profile(path: Path | None = None) -> ProfileData:
    """Read and parse the stored profile JSON (public API).

    This is the public contract wrapping :func:`_read_profile`. Prefer this
    function in external consumers; ``_read_profile`` is kept for internal use.

    Args:
        path: Path to the profile JSON file. If None, uses the module-level
              _PROFILE_PATH (resolved at call time so tests can monkeypatch it).

    Returns:
        ProfileData parsed from the file.

    Raises:
        ValueError: if the file does not exist or its content is malformed.
    """
    return _read_profile(path=path)


def _write_profile(profile: ProfileData, path: Path = _PROFILE_PATH) -> None:
    """Atomically write the profile as pretty-printed JSON.

    Creates parent directories if they do not exist. Uses a temp-file +
    rename pattern to avoid corrupting an existing profile on partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=".profile_tmp_", suffix=".json"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _persist(profile: ProfileData, *, overwrite: bool) -> ProfileSetupResult:
    """Persist an already-extracted profile to disk.

    Args:
        profile:   The structured ProfileData (extracted by Claude from the CV).
        overwrite: If False, raises ValueError when profile.json already exists.

    Returns:
        ProfileSetupResult with success=True on success.

    Raises:
        ValueError: when overwrite is False and a profile already exists.
    """
    if not overwrite and _PROFILE_PATH.exists():
        raise ValueError(
            "Failed to set up profile: a profile already exists. "
            "Use update_profile to replace it."
        )

    _write_profile(profile, _PROFILE_PATH)

    experience_years = sum((e.duration_years or 0.0) for e in profile.experience)

    return ProfileSetupResult(
        success=True,
        storage_path=str(_PROFILE_PATH.resolve()),
        profile_summary={
            "name": profile.name,
            "skills_count": len(profile.skills),
            "experience_years": experience_years,
        },
    )


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def setup_profile(profile: ProfileData) -> ProfileSetupResult:
    """Save a structured profile extracted from the user's CV.

    Before calling this, read the user's CV file (.pdf or .docx) and extract
    the structured fields (name, email, location, skills, experience,
    education, languages, summary) into the ``profile`` argument. This tool
    only persists that data to ~/.config/runway-mcp/profile.json.

    Fails if a profile already exists — use update_profile to replace it.

    Args:
        profile: The structured profile extracted from the candidate's CV.

    Returns:
        ProfileSetupResult with success=True and a profile summary on success,
        or success=False with an error_message on failure.
    """
    try:
        return _persist(profile, overwrite=False)
    except ValueError as exc:
        return ProfileSetupResult(success=False, error_message=str(exc))


def update_profile(profile: ProfileData) -> ProfileSetupResult:
    """Save a structured profile, overwriting any existing one.

    Behaves like setup_profile but overwrites an existing profile without
    failing. If no profile exists, one is created. Read and extract the CV
    into ``profile`` before calling — this tool only persists the data.

    Args:
        profile: The structured profile extracted from the candidate's CV.

    Returns:
        ProfileSetupResult with success=True and a profile summary on success,
        or success=False with an error_message on failure.
    """
    try:
        return _persist(profile, overwrite=True)
    except ValueError as exc:
        return ProfileSetupResult(success=False, error_message=str(exc))


def get_profile() -> GetProfileResult:
    """Return the stored candidate profile.

    Use this to load the saved profile so you can score a job posting against
    it. Returns a structured error envelope (never raises) when no profile
    exists yet or the stored file is corrupt.

    Returns:
        GetProfileResult with success=True and the profile on success, or
        success=False with error/message on failure.
    """
    if not _PROFILE_PATH.exists():
        return GetProfileResult(
            success=False,
            error="no_profile",
            message="No profile found. Run setup_profile first.",
        )
    try:
        profile = _read_profile()
    except ValueError as exc:
        return GetProfileResult(success=False, error="corrupt", message=str(exc))

    return GetProfileResult(success=True, profile=profile)

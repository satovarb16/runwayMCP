"""Profile management tools: setup_profile and update_profile.

Reads a CV file (.pdf or .docx) as bytes, forwards to the host Claude via MCP
sampling, validates the returned JSON against the ProfileData schema, and
persists the result to ~/.config/runway-mcp/profile.json.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent
from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PROFILE_PATH: Path = Path.home() / ".config" / "runway-mcp" / "profile.json"
_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB soft cap
_SUPPORTED: frozenset[str] = frozenset({".pdf", ".docx"})

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
    naturally returns and simplifies downstream consumers like analyze_match.
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_cv_bytes(file_path: str) -> tuple[bytes, str]:
    """Read CV bytes and validate the file.

    Returns:
        (raw_bytes, lowercase_extension)

    Raises:
        ValueError: on missing file, unsupported extension, empty file, or file > 5 MB.
    """
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise ValueError(f"Failed to set up profile: file not found: {file_path}")

    ext = path.suffix.lower()
    if ext not in _SUPPORTED:
        raise ValueError(
            f"Failed to set up profile: unsupported file format '{ext}'. "
            f"Supported formats: {', '.join(sorted(_SUPPORTED))}"
        )

    raw = path.read_bytes()

    if len(raw) == 0:
        raise ValueError("Failed to set up profile: file is empty")

    if len(raw) > _MAX_BYTES:
        mb = len(raw) / (1024 * 1024)
        raise ValueError(
            f"Failed to set up profile: file is too large ({mb:.1f} MB). "
            f"Maximum allowed size is 5 MB"
        )

    return raw, ext


def _build_extraction_prompt(b64: str, ext: str) -> list[SamplingMessage]:
    """Build the list of SamplingMessages that asks Claude to extract CV data."""
    schema_description = """
{
  "name": "string or null",
  "email": "string or null",
  "location": "string or null",
  "skills": ["list", "of", "strings"],
  "experience": [
    {
      "company": "string",
      "title": "string",
      "duration_years": "float or null",
      "description": "string or null"
    }
  ],
  "education": [
    {
      "institution": "string",
      "degree": "string or null",
      "field": "string or null",
      "year": "integer or null"
    }
  ],
  "languages": ["list", "of", "strings"],
  "summary": "string"
}"""

    user_content = (
        f"Extract the CV data from the following base64-encoded {ext.lstrip('.')} file "
        f"and return ONLY a JSON object matching this schema (no markdown fences, no prose):\n"
        f"{schema_description}\n\n"
        f"Base64 file content:\n{b64}"
    )

    return [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=user_content),
        )
    ]


def _parse_sampled_json(raw: str) -> ProfileData:
    """Parse Claude's response text into a validated ProfileData.

    Strips markdown fences if present. Raises ValueError on malformed or
    schema-invalid JSON.
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        return ProfileData.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise ValueError(
            f"Failed to set up profile: could not parse Claude's response as valid profile JSON: {exc}"
        ) from exc


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


async def _ingest(
    file_path: str,
    ctx: Context,
    *,
    overwrite: bool,
) -> ProfileSetupResult:
    """Orchestrate: read → sample → parse → write → return result.

    Args:
        file_path: Absolute or relative path to the CV file.
        ctx:       FastMCP Context (provides MCP session for sampling).
        overwrite: If False, raises ValueError when profile.json already exists.

    Returns:
        ProfileSetupResult with success=True on success.

    Raises:
        ValueError: on validation, parse, or overwrite-guard errors.
    """
    if not overwrite and _PROFILE_PATH.exists():
        raise ValueError(
            "Failed to set up profile: a profile already exists. "
            "Use update_profile to replace it."
        )

    raw_bytes, ext = _read_cv_bytes(file_path)
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    messages = _build_extraction_prompt(b64, ext)

    result = await ctx.session.create_message(
        messages=messages,
        system_prompt=(
            "You are a CV parser. Extract structured data from the provided CV. "
            "Return ONLY a JSON object — no markdown fences, no prose, no explanation."
        ),
        max_tokens=4000,
    )

    # result.content is SamplingContent (TextContent | ImageContent | AudioContent)
    if not isinstance(result.content, TextContent):
        raise ValueError(
            "Failed to set up profile: Claude returned non-text content during sampling"
        )

    profile = _parse_sampled_json(result.content.text)
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


async def setup_profile(file_path: str, ctx: Context) -> ProfileSetupResult:
    """Parse a CV file and save a structured profile for job-match analysis.

    Reads a local .pdf or .docx CV file (max 5 MB), extracts structured data
    via MCP sampling, and persists the profile to
    ~/.config/runway-mcp/profile.json.

    Fails with an error if a profile already exists — use update_profile to
    replace an existing profile.

    Args:
        file_path: Absolute or relative path to the CV file (.pdf or .docx).
        ctx:       Injected by FastMCP — provides access to the MCP session.

    Returns:
        ProfileSetupResult with success=True and a profile summary on success,
        or success=False with an error_message on failure.
    """
    try:
        return await _ingest(file_path, ctx, overwrite=False)
    except ValueError as exc:
        return ProfileSetupResult(success=False, error_message=str(exc))


async def update_profile(file_path: str, ctx: Context) -> ProfileSetupResult:
    """Parse a CV file and overwrite the existing profile.

    Behaves identically to setup_profile but overwrites any existing profile
    without prompting. If no profile exists, one is created.

    Args:
        file_path: Absolute or relative path to the CV file (.pdf or .docx).
        ctx:       Injected by FastMCP — provides access to the MCP session.

    Returns:
        ProfileSetupResult with success=True and a profile summary on success,
        or success=False with an error_message on failure.
    """
    try:
        return await _ingest(file_path, ctx, overwrite=True)
    except ValueError as exc:
        return ProfileSetupResult(success=False, error_message=str(exc))

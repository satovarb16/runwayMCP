"""Match analysis tool: analyze_match.

Compares a fetched JobPostingResult against the stored user profile and returns
a structured, factual scoring result. Uses MCP sampling (ctx.session.create_message)
to delegate comparison to the host Claude model.
"""

from __future__ import annotations

import re

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent
from pydantic import BaseModel, Field, ValidationError

from tools.jobs import JobPostingResult
from tools.profile import ProfileData, load_profile


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class _MatchPayload(BaseModel):
    """Claude's strict output contract — validated before mapping to MatchResult."""

    score: int = Field(ge=0, le=100)
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    experience_match: str | None = None
    education_match: str | None = None
    summary: str | None = None


class MatchResult(BaseModel):
    """Public tool return envelope."""

    success: bool
    score: int | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    experience_match: str | None = None
    education_match: str | None = None
    summary: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_SCHEMA_BLOCK = """\
{
  "score": <integer 0-100>,
  "matched_skills": ["list", "of", "matching", "skills"],
  "missing_skills": ["list", "of", "missing", "skills"],
  "experience_match": "factual description of experience alignment or null",
  "education_match": "factual description of education alignment or null",
  "summary": "factual summary of overall match or null"
}"""


def _build_match_prompt(
    job: JobPostingResult,
    profile: ProfileData,
) -> list[SamplingMessage]:
    """Build the sampling messages that ask Claude to compare job vs profile.

    The system prompt sets a factual-only comparator role and explicitly forbids
    recommendations, advice, or subjective verdicts. The user message embeds both
    the job and profile as JSON plus an explicit output schema.
    """
    user_content = (
        "Compare the following job posting against the candidate profile below. "
        "Return ONLY a JSON object matching the schema — no markdown fences, "
        "no prose, no advice, no recommendations, no subjective verdicts. "
        "Use factual and objective language only.\n\n"
        "## Output schema\n"
        f"{_SCHEMA_BLOCK}\n\n"
        "## Job posting (JSON)\n"
        f"{job.model_dump_json()}\n\n"
        "## Candidate profile (JSON)\n"
        f"{profile.model_dump_json()}"
    )

    return [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=user_content),
        )
    ]


def _parse_match_json(raw: str) -> MatchResult:
    """Parse Claude's response into a validated MatchResult.

    Strips markdown fences if present. Raises ValueError on malformed JSON
    or schema violations (including score outside [0, 100]).
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        payload = _MatchPayload.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise ValueError(
            f"Failed to parse match JSON from Claude's response: {exc}"
        ) from exc

    return MatchResult(
        success=True,
        score=payload.score,
        matched_skills=payload.matched_skills,
        missing_skills=payload.missing_skills,
        experience_match=payload.experience_match,
        education_match=payload.education_match,
        summary=payload.summary,
    )


async def _run(job: JobPostingResult, ctx: Context) -> MatchResult:
    """Core logic — raises ValueError on any failure."""
    profile = load_profile()

    messages = _build_match_prompt(job, profile)

    result = await ctx.session.create_message(
        messages=messages,
        system_prompt=(
            "You are a factual CV-to-job comparator. "
            "Your task is to objectively compare a job posting against a candidate profile. "
            "Do NOT provide recommendations, advice, or subjective verdicts. "
            "Return ONLY valid JSON — no markdown fences, no prose, no explanation."
        ),
        max_tokens=2000,
    )

    if not isinstance(result.content, TextContent):
        raise ValueError(
            f"Claude returned non-text content during match analysis: "
            f"got {type(result.content).__name__}"
        )

    return _parse_match_json(result.content.text)


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------


async def analyze_match(job: JobPostingResult, ctx: Context) -> MatchResult:
    """Score a job posting against the stored profile.

    Reads the profile from ~/.config/runway-mcp/profile.json, sends both the
    job posting and profile to the host Claude model via MCP sampling, and
    returns a structured, factual scoring result.

    The tool does NOT fetch the job internally — the caller must supply an
    already-fetched JobPostingResult (e.g. from fetch_job_posting).

    Args:
        job: A JobPostingResult obtained from fetch_job_posting.
        ctx: Injected by FastMCP — provides access to the MCP session.

    Returns:
        MatchResult with success=True and scoring data on success, or
        success=False with an error_message on any failure.
    """
    try:
        return await _run(job, ctx)
    except ValueError as exc:
        return MatchResult(success=False, error_message=str(exc))

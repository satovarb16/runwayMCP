"""Unit tests for tools/match.py — TDD RED/GREEN per SDD task list."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from mcp.types import TextContent, CreateMessageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_MATCH_JSON = json.dumps({
    "score": 72,
    "matched_skills": ["Python", "FastAPI"],
    "missing_skills": ["Go"],
    "experience_match": "Candidate has 3 years; role requires 2 years.",
    "education_match": "BSc in CS matches requirement.",
    "summary": "Candidate meets 72% of the job requirements based on skills and experience.",
})

_SAMPLE_PROFILE_JSON = json.dumps({
    "name": "Jane Doe",
    "email": "jane@example.com",
    "location": "NYC",
    "skills": ["Python", "FastAPI"],
    "experience": [{"company": "Acme", "title": "SWE", "duration_years": 3.0}],
    "education": [{"institution": "MIT", "degree": "BSc", "field": "CS", "year": 2018}],
    "languages": ["English"],
    "summary": "Engineer.",
})


def _make_job():
    """Return a minimal JobPostingResult."""
    from tools.jobs import JobPostingResult
    return JobPostingResult(
        title="Backend Engineer",
        company="StartupCo",
        country="US",
        location="Remote",
        description="We need Python, FastAPI, Go. 2+ years experience required.",
    )


def _make_ctx(response_json: str = _VALID_MATCH_JSON) -> MagicMock:
    """Return a fake Context whose session.create_message returns the given JSON."""
    ctx = MagicMock()
    result = MagicMock(spec=CreateMessageResult)
    result.content = TextContent(type="text", text=response_json)
    ctx.session.create_message = AsyncMock(return_value=result)
    return ctx


def _patch_profile_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _PROFILE_PATH in tools.profile to a temp location."""
    import tools.profile as profile_mod
    new_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", new_path)
    return new_path


# ---------------------------------------------------------------------------
# Phase 2.1 RED: _parse_match_json
# ---------------------------------------------------------------------------


def test_parse_match_json_valid():
    """Valid JSON returns a MatchResult with success=True."""
    from tools.match import _parse_match_json

    result = _parse_match_json(_VALID_MATCH_JSON)

    assert result.success is True
    assert result.score == 72
    assert "Python" in result.matched_skills
    assert "Go" in result.missing_skills
    assert result.error_message is None


def test_parse_match_json_fenced():
    """JSON wrapped in markdown fences is stripped and parsed correctly."""
    from tools.match import _parse_match_json

    fenced = f"```json\n{_VALID_MATCH_JSON}\n```"
    result = _parse_match_json(fenced)

    assert result.success is True
    assert result.score == 72


def test_parse_match_json_malformed():
    """Non-JSON text raises ValueError."""
    from tools.match import _parse_match_json

    with pytest.raises(ValueError, match="(?i)parse|invalid|json"):
        _parse_match_json("this is not json at all")


def test_parse_match_json_score_150():
    """Score outside [0, 100] raises ValueError."""
    from tools.match import _parse_match_json

    bad_json = json.dumps({
        "score": 150,
        "matched_skills": [],
        "missing_skills": [],
        "experience_match": "n/a",
        "education_match": "n/a",
        "summary": "test",
    })

    with pytest.raises(ValueError):
        _parse_match_json(bad_json)


# ---------------------------------------------------------------------------
# Phase 2.3 RED: _build_match_prompt
# ---------------------------------------------------------------------------


def test_build_match_prompt_embeds_job():
    """Prompt user message contains the serialized job data."""
    from tools.match import _build_match_prompt
    from tools.profile import ProfileData

    job = _make_job()
    profile = ProfileData.model_validate_json(_SAMPLE_PROFILE_JSON)

    messages = _build_match_prompt(job, profile)

    combined = " ".join(
        m.content.text for m in messages if hasattr(m.content, "text")
    )
    assert "Backend Engineer" in combined
    assert "StartupCo" in combined


def test_build_match_prompt_embeds_profile():
    """Prompt user message contains the serialized profile data."""
    from tools.match import _build_match_prompt
    from tools.profile import ProfileData

    job = _make_job()
    profile = ProfileData.model_validate_json(_SAMPLE_PROFILE_JSON)

    messages = _build_match_prompt(job, profile)

    combined = " ".join(
        m.content.text for m in messages if hasattr(m.content, "text")
    )
    assert "Jane Doe" in combined
    assert "Python" in combined


def test_build_match_prompt_forbids_advice():
    """System / user prompts must explicitly prohibit recommendations/advice."""
    from tools.match import _build_match_prompt
    from tools.profile import ProfileData

    job = _make_job()
    profile = ProfileData.model_validate_json(_SAMPLE_PROFILE_JSON)

    messages = _build_match_prompt(job, profile)

    combined = " ".join(
        m.content.text for m in messages if hasattr(m.content, "text")
    )
    # At least one of these prohibition words must appear
    prohibition_terms = ["no recommendation", "no advice", "factual", "objective", "forbid"]
    assert any(term in combined.lower() for term in prohibition_terms), (
        f"Prompt must prohibit advice/recommendations. Got: {combined[:300]}"
    )


# ---------------------------------------------------------------------------
# Phase 2.5 RED: analyze_match integration (mocked ctx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_match_happy_path(tmp_path, monkeypatch):
    """Happy path: profile exists, sampling returns valid JSON → success=True."""
    from tools.match import analyze_match

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    job = _make_job()
    ctx = _make_ctx(_VALID_MATCH_JSON)

    result = await analyze_match(job, ctx)

    assert result.success is True
    assert result.score == 72
    assert result.error_message is None
    assert isinstance(result.matched_skills, list)
    assert isinstance(result.missing_skills, list)


@pytest.mark.asyncio
async def test_analyze_match_no_profile(tmp_path, monkeypatch):
    """No profile file → success=False, no sampling call."""
    from tools.match import analyze_match

    _patch_profile_path(monkeypatch, tmp_path)
    # Do NOT write profile.json

    job = _make_job()
    ctx = _make_ctx()

    result = await analyze_match(job, ctx)

    assert result.success is False
    assert result.score is None
    assert result.error_message is not None
    ctx.session.create_message.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_match_bad_json(tmp_path, monkeypatch):
    """Sampling returns malformed JSON → success=False."""
    from tools.match import analyze_match

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    job = _make_job()
    ctx = _make_ctx("not valid json at all")

    result = await analyze_match(job, ctx)

    assert result.success is False
    assert result.score is None
    assert result.error_message is not None


@pytest.mark.asyncio
async def test_analyze_match_non_text_content(tmp_path, monkeypatch):
    """Sampling returns non-TextContent → success=False."""
    from tools.match import analyze_match
    from mcp.types import ImageContent

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    job = _make_job()

    ctx = MagicMock()
    result_mock = MagicMock(spec=CreateMessageResult)
    result_mock.content = ImageContent(type="image", data="abc", mimeType="image/png")
    ctx.session.create_message = AsyncMock(return_value=result_mock)

    result = await analyze_match(job, ctx)

    assert result.success is False
    assert result.score is None
    assert result.error_message is not None


@pytest.mark.asyncio
async def test_analyze_match_score_out_of_range(tmp_path, monkeypatch):
    """Sampling returns score > 100 → success=False."""
    from tools.match import analyze_match

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    bad_json = json.dumps({
        "score": 150,
        "matched_skills": [],
        "missing_skills": [],
        "experience_match": "n/a",
        "education_match": "n/a",
        "summary": "test",
    })

    job = _make_job()
    ctx = _make_ctx(bad_json)

    result = await analyze_match(job, ctx)

    assert result.success is False
    assert result.score is None


@pytest.mark.asyncio
async def test_analyze_match_empty_skills(tmp_path, monkeypatch):
    """Job with no skills → success=True, empty matched/missing lists."""
    from tools.match import analyze_match
    from tools.jobs import JobPostingResult

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    job = JobPostingResult(
        title="Generic Role",
        company="Corp",
        description="No specific skills listed.",
    )

    empty_skills_json = json.dumps({
        "score": 50,
        "matched_skills": [],
        "missing_skills": [],
        "experience_match": "Meets requirement.",
        "education_match": "Meets requirement.",
        "summary": "Candidate meets 50% of requirements.",
    })

    ctx = _make_ctx(empty_skills_json)

    result = await analyze_match(job, ctx)

    assert result.success is True
    assert result.matched_skills == []
    assert result.missing_skills == []
    assert result.score == 50

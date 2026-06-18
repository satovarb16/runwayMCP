"""Tests for tools/analyze.py — analyze_job orchestrator tool.

Strict TDD: RED tests written first, then implementation makes them GREEN.
Covers SC-01..SC-12 from the spec.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from tools.jobs import JobPostingResult
from tools.visa import VisaResult, Verdict
from tools.match import MatchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_result(
    title: str = "Backend Engineer",
    company: str = "Acme Corp",
    url: str = "https://example.com/job/123",
) -> JobPostingResult:
    return JobPostingResult(
        title=title,
        company=company,
        source_url=url,
    )


def _make_visa_result(
    verdict: Verdict = Verdict.GREEN,
    total_filings: int = 15,
    approval_rate: float = 0.92,
) -> VisaResult:
    return VisaResult(
        company="Acme Corp",
        total_filings=total_filings,
        approval_rate=approval_rate,
        verdict=verdict,
    )


def _make_match_result(
    success: bool = True,
    score: int | None = 80,
    matched_skills: list[str] | None = None,
    missing_skills: list[str] | None = None,
    summary: str | None = "Strong match.",
    error_message: str | None = None,
) -> MatchResult:
    return MatchResult(
        success=success,
        score=score,
        matched_skills=matched_skills if matched_skills is not None else ["Python"],
        missing_skills=missing_skills if missing_skills is not None else ["Kubernetes"],
        summary=summary,
        error_message=error_message,
    )


def _make_ctx() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# T-01: Pydantic models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """T-01 — Verify model field names, types, and defaults."""

    def test_job_summary_fields(self):
        from tools.analyze import JobSummary

        js = JobSummary(title="Engineer", company="Acme", url="https://example.com")
        assert js.title == "Engineer"
        assert js.company == "Acme"
        assert js.url == "https://example.com"

    def test_visa_summary_defaults(self):
        from tools.analyze import VisaSummary

        vs = VisaSummary(verdict="GREEN", filings=10, approval_rate=0.9)
        assert vs.error is None

    def test_visa_summary_fields(self):
        from tools.analyze import VisaSummary

        vs = VisaSummary(verdict="YELLOW", filings=5, approval_rate=0.75, error="test")
        assert vs.verdict == "YELLOW"
        assert vs.filings == 5
        assert vs.approval_rate == 0.75
        assert vs.error == "test"

    def test_match_summary_defaults(self):
        from tools.analyze import MatchSummary

        ms = MatchSummary()
        assert ms.score is None
        assert ms.matched_skills == []
        assert ms.missing_skills == []
        assert ms.summary is None
        assert ms.error is None

    def test_match_summary_fields(self):
        from tools.analyze import MatchSummary

        ms = MatchSummary(
            score=80,
            matched_skills=["Python"],
            missing_skills=["Go"],
            summary="Good match.",
        )
        assert ms.score == 80
        assert "Python" in ms.matched_skills
        assert "Go" in ms.missing_skills

    def test_analyze_job_result_all_optional(self):
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert result.job is None
        assert result.visa is None
        assert result.match is None
        assert result.recommendation is None
        assert result.error is None
        assert result.message is None

    def test_analyze_job_result_no_confidence_field(self):
        """The spec forbids a 'confidence' field anywhere in the envelope."""
        from tools.analyze import AnalyzeJobResult, VisaSummary

        vs = VisaSummary(verdict="GREEN", filings=10, approval_rate=0.9)
        assert not hasattr(vs, "confidence"), "VisaSummary must NOT have a 'confidence' field"

        result = AnalyzeJobResult()
        assert not hasattr(result, "confidence"), "AnalyzeJobResult must NOT have a 'confidence' field"


# ---------------------------------------------------------------------------
# T-02: _compute_recommendation pure function
# ---------------------------------------------------------------------------


class TestComputeRecommendation:
    """T-02 — Full truth table including boundaries SC-11 and SC-12."""

    def _call(self, verdict: str, score: int | None) -> str | None:
        from tools.analyze import _compute_recommendation
        return _compute_recommendation(verdict, score)

    def test_score_none_returns_none(self):
        assert self._call("GREEN", None) is None

    def test_score_none_any_verdict_returns_none(self):
        for verdict in ("GREEN", "YELLOW", "RED", "UNKNOWN"):
            assert self._call(verdict, None) is None

    def test_red_verdict_any_score_returns_skip(self):
        assert self._call("RED", 75) == "SKIP"
        assert self._call("RED", 100) == "SKIP"
        assert self._call("RED", 0) == "SKIP"

    def test_score_below_40_returns_skip(self):
        assert self._call("GREEN", 39) == "SKIP"
        assert self._call("YELLOW", 0) == "SKIP"
        assert self._call("UNKNOWN", 10) == "SKIP"

    def test_skip_beats_apply_green_plus_low_score(self):
        """SC: GREEN verdict + score 30 → SKIP (SKIP has precedence)."""
        assert self._call("GREEN", 30) == "SKIP"

    def test_green_plus_score_70_returns_apply(self):
        """SC-11: boundary — score exactly 70 → APPLY."""
        assert self._call("GREEN", 70) == "APPLY"

    def test_green_plus_score_above_70_returns_apply(self):
        assert self._call("GREEN", 80) == "APPLY"
        assert self._call("GREEN", 100) == "APPLY"

    def test_score_exactly_40_returns_consider_not_skip(self):
        """SC-12: SKIP fires only when score < 40; score==40 → CONSIDER."""
        assert self._call("GREEN", 40) == "CONSIDER"
        assert self._call("YELLOW", 40) == "CONSIDER"

    def test_yellow_score_60_returns_consider(self):
        """SC-04."""
        assert self._call("YELLOW", 60) == "CONSIDER"

    def test_unknown_score_75_returns_consider(self):
        """SC-05."""
        assert self._call("UNKNOWN", 75) == "CONSIDER"

    def test_green_score_69_returns_consider(self):
        """Just below APPLY threshold."""
        assert self._call("GREEN", 69) == "CONSIDER"


# ---------------------------------------------------------------------------
# T-03: Result mappers
# ---------------------------------------------------------------------------


class TestResultMappers:
    """T-03 — Verify visa and match mapper helpers."""

    def test_map_visa_verdict_uppercase(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result(verdict=Verdict.GREEN, total_filings=15, approval_rate=0.92)
        summary = _map_visa(visa)

        assert summary.verdict == "GREEN"  # must be uppercase

    def test_map_visa_filings_from_total_filings(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result(total_filings=42)
        summary = _map_visa(visa)

        assert summary.filings == 42

    def test_map_visa_approval_rate_passthrough(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result(approval_rate=0.87)
        summary = _map_visa(visa)

        assert summary.approval_rate == 0.87

    def test_map_visa_no_confidence_field(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result()
        summary = _map_visa(visa)

        assert not hasattr(summary, "confidence"), "Mapped VisaSummary must NOT have 'confidence'"

    def test_map_visa_error_defaults_none(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result()
        summary = _map_visa(visa)

        assert summary.error is None

    def test_map_match_success_result(self):
        from tools.analyze import _map_match

        match = _make_match_result(score=80, matched_skills=["Python"], missing_skills=["Go"])
        summary = _map_match(match)

        assert summary.score == 80
        assert "Python" in summary.matched_skills
        assert "Go" in summary.missing_skills
        assert summary.error is None

    def test_map_match_failed_result(self):
        from tools.analyze import _map_match

        match = _make_match_result(success=False, score=None, error_message="sampling failed")
        summary = _map_match(match)

        assert summary.score is None
        assert summary.error is not None

    def test_map_visa_yellow_verdict_uppercase(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result(verdict=Verdict.YELLOW, total_filings=3, approval_rate=0.6)
        summary = _map_visa(visa)

        assert summary.verdict == "YELLOW"

    def test_map_visa_red_verdict_uppercase(self):
        from tools.analyze import _map_visa

        visa = _make_visa_result(verdict=Verdict.RED, total_filings=0, approval_rate=0.0)
        summary = _map_visa(visa)

        assert summary.verdict == "RED"


# ---------------------------------------------------------------------------
# T-04: Orchestration happy path
# ---------------------------------------------------------------------------


class TestAnalyzeJobHappyPath:
    """T-04 — SC-01..SC-04 with all sub-tools mocked."""

    @pytest.mark.asyncio
    async def test_sc01_apply(self, monkeypatch):
        """SC-01: GREEN visa, score=80 → recommendation APPLY."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN, total_filings=15, approval_rate=0.92),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=80)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.error is None
        assert result.job is not None
        assert result.job.title == "Backend Engineer"
        assert result.visa is not None
        assert result.visa.verdict == "GREEN"
        assert result.visa.filings == 15
        assert result.visa.approval_rate == 0.92
        assert result.match is not None
        assert result.match.score == 80
        assert result.recommendation == "APPLY"

    @pytest.mark.asyncio
    async def test_sc02_skip_red_verdict(self, monkeypatch):
        """SC-02: RED visa, score=75 → SKIP."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.RED, total_filings=0, approval_rate=0.0),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=75)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.visa.verdict == "RED"
        assert result.recommendation == "SKIP"

    @pytest.mark.asyncio
    async def test_sc03_skip_low_score(self, monkeypatch):
        """SC-03: GREEN visa, score=35 → SKIP."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=35)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.visa.verdict == "GREEN"
        assert result.match.score == 35
        assert result.recommendation == "SKIP"

    @pytest.mark.asyncio
    async def test_sc04_consider(self, monkeypatch):
        """SC-04: YELLOW visa, score=60 → CONSIDER."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.YELLOW, total_filings=3, approval_rate=0.6),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=60)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.recommendation == "CONSIDER"

    @pytest.mark.asyncio
    async def test_sc11_boundary_score_70_apply(self, monkeypatch):
        """SC-11: GREEN + score exactly 70 → APPLY."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=70)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.recommendation == "APPLY"

    @pytest.mark.asyncio
    async def test_sc12_boundary_score_40_consider(self, monkeypatch):
        """SC-12: GREEN + score exactly 40 → CONSIDER (SKIP fires only at <40)."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(score=40)),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.recommendation == "CONSIDER"

    @pytest.mark.asyncio
    async def test_analyze_job_never_raises(self, monkeypatch):
        """analyze_job must NEVER raise — all failures returned in envelope."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(
            analyze_mod, "fetch_job_posting",
            lambda url: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )

        from tools.analyze import analyze_job

        # Should not raise
        result = await analyze_job("https://example.com/job/123", _make_ctx())
        assert result is not None


# ---------------------------------------------------------------------------
# T-05: Failure paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    """T-05 — SC-05, SC-07, SC-08."""

    @pytest.mark.asyncio
    async def test_sc07_fetch_fails_stops_orchestration(self, monkeypatch):
        """SC-07: fetch raises → error='fetch_failed', visa not called."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())

        visa_spy = MM(side_effect=lambda company: _make_visa_result())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting",
                            lambda url: (_ for _ in ()).throw(ValueError("page not found")))
        monkeypatch.setattr(analyze_mod, "check_visa_sponsorship", visa_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.error == "fetch_failed"
        assert "page not found" in result.message
        assert result.visa is None
        assert result.match is None
        assert result.recommendation is None
        visa_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_sc05_visa_fails_continues_to_match(self, monkeypatch):
        """SC-05: visa raises → UNKNOWN verdict, match IS called, CONSIDER (score>=40)."""
        from tools import analyze as analyze_mod

        match_spy = AsyncMock(return_value=_make_match_result(score=75))
        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: (_ for _ in ()).throw(RuntimeError("USCIS down")),
        )
        monkeypatch.setattr(analyze_mod, "analyze_match", match_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.visa.verdict == "UNKNOWN"
        assert result.visa.error is not None
        assert result.match.score == 75
        assert result.recommendation == "CONSIDER"
        match_spy.assert_called_once()

    @pytest.mark.asyncio
    async def test_sc08_match_fails_null_recommendation(self, monkeypatch):
        """SC-08: match returns success=False → match.score=None, recommendation=None."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(return_value=_make_match_result(
                success=False, score=None, error_message="sampling failed"
            )),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.visa.verdict == "GREEN"
        assert result.match.score is None
        assert result.match.error is not None
        assert result.recommendation is None

    @pytest.mark.asyncio
    async def test_match_raises_treated_as_failure(self, monkeypatch):
        """analyze_match raising an exception → same as success=False."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: MagicMock())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship",
            lambda company: _make_visa_result(verdict=Verdict.GREEN),
        )
        monkeypatch.setattr(
            analyze_mod, "analyze_match",
            AsyncMock(side_effect=RuntimeError("unexpected match error")),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.match is not None
        assert result.match.score is None
        assert result.recommendation is None


# ---------------------------------------------------------------------------
# T-06: Profile precondition
# ---------------------------------------------------------------------------


class TestProfilePrecondition:
    """T-06 — SC-06: missing profile → structured error, fetch NOT called."""

    @pytest.mark.asyncio
    async def test_sc06_no_profile_file_not_found_error(self, monkeypatch):
        """FileNotFoundError from _read_profile → no_profile error envelope."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        fetch_spy = MM(side_effect=lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "_read_profile",
            lambda: (_ for _ in ()).throw(FileNotFoundError("no profile")),
        )
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", fetch_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.error == "no_profile"
        assert "setup_profile" in result.message
        assert result.job is None
        assert result.visa is None
        assert result.match is None
        assert result.recommendation is None
        fetch_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_sc06_no_profile_value_error(self, monkeypatch):
        """ValueError from _read_profile (e.g. empty profile) → no_profile envelope."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        fetch_spy = MM(side_effect=lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod, "_read_profile",
            lambda: (_ for _ in ()).throw(ValueError("No profile found. Run setup_profile first.")),
        )
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", fetch_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.error == "no_profile"
        assert "setup_profile" in result.message
        fetch_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_sc06_no_profile_returns_none(self, monkeypatch):
        """_read_profile returning None → no_profile envelope."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        fetch_spy = MM(side_effect=lambda url: _make_job_result())
        monkeypatch.setattr(analyze_mod, "_read_profile", lambda: None)
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", fetch_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123", _make_ctx())

        assert result.error == "no_profile"
        assert result.job is None
        fetch_spy.assert_not_called()


# ---------------------------------------------------------------------------
# T-07: Server registration
# ---------------------------------------------------------------------------


class TestServerRegistration:
    """T-07 — SC-09 (existing tools unchanged) and SC-10 (analyze_job registered)."""

    def test_sc10_analyze_job_importable_from_tools_analyze(self):
        """analyze_job must be importable from tools.analyze."""
        from tools.analyze import analyze_job

        assert callable(analyze_job)

    def test_sc10_analyze_job_registered_in_server(self):
        """server.py must register analyze_job alongside the existing tools."""
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert "analyze_job" in tool_names, (
            f"analyze_job not found in registered tools: {tool_names}"
        )

    def test_sc09_existing_tools_still_registered(self):
        """The four existing tools must remain registered after adding analyze_job."""
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        for expected in ("fetch_job_posting", "check_visa_sponsorship",
                         "setup_profile", "update_profile", "analyze_match"):
            assert expected in tool_names, f"{expected} missing from tool registry"

    def test_analyze_job_result_is_pydantic_model(self):
        """AnalyzeJobResult must be a Pydantic BaseModel."""
        from pydantic import BaseModel
        from tools.analyze import AnalyzeJobResult

        assert issubclass(AnalyzeJobResult, BaseModel)

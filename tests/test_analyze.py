"""Tests for tools/analyze.py — analyze_job orchestrator (Option A).

analyze_job gathers FACTS (job, visa, resume) + a scoring guide. The match
score and APPLY/CONSIDER/SKIP recommendation are produced by the
conversation-side Claude, NOT the server — so this suite asserts the envelope
contents, not a server-computed score/recommendation.
"""

from __future__ import annotations

import pytest

from tools.jobs import JobPostingResult
from tools.visa import VisaResult, Verdict
from tools.resumes import ResumeStore, ResumeVersion


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


def _make_resume(
    id: str = "base-1",
    label: str = "Base",
    content: str = "Jane Doe — Python engineer",
    parent_id: str | None = None,
    job_url: str | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> ResumeVersion:
    return ResumeVersion(
        id=id,
        label=label,
        content=content,
        parent_id=parent_id,
        job_url=job_url,
        created_at=created_at,
    )


def _make_resume_store(*versions: ResumeVersion) -> ResumeStore:
    return ResumeStore(versions=list(versions))


# ---------------------------------------------------------------------------
# T-01: Pydantic models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Verify model field names, types, and defaults."""

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

    def test_scoring_guide_fields(self):
        from tools.analyze import ScoringGuide

        guide = ScoringGuide(instructions="do it", recommendation_rules=["a", "b"])
        assert guide.instructions == "do it"
        assert guide.recommendation_rules == ["a", "b"]

    def test_analyze_job_result_all_optional(self):
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert result.job is None
        assert result.visa is None
        assert result.resume is None
        assert result.scoring_guide is None
        assert result.error is None
        assert result.message is None

    def test_analyze_job_result_no_match_or_recommendation_field(self):
        """Option A: the server no longer computes match/recommendation."""
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert not hasattr(result, "match"), "Server must NOT compute a match"
        assert not hasattr(result, "recommendation"), (
            "Server must NOT compute a recommendation"
        )

    def test_analyze_job_result_profile_field_renamed_to_resume(self):
        """PR5: the envelope field is `resume`, `profile` must be gone."""
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert not hasattr(result, "profile"), (
            "`profile` field must be renamed to `resume`"
        )
        assert hasattr(result, "resume")

    def test_analyze_job_result_no_confidence_field(self):
        from tools.analyze import AnalyzeJobResult, VisaSummary

        vs = VisaSummary(verdict="GREEN", filings=10, approval_rate=0.9)
        assert not hasattr(vs, "confidence")

        result = AnalyzeJobResult()
        assert not hasattr(result, "confidence")


# ---------------------------------------------------------------------------
# T-02: Visa mapper
# ---------------------------------------------------------------------------


class TestVisaMapper:
    def test_map_visa_verdict_uppercase(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result(verdict=Verdict.GREEN))
        assert summary.verdict == "GREEN"

    def test_map_visa_filings_from_total_filings(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result(total_filings=42))
        assert summary.filings == 42

    def test_map_visa_approval_rate_passthrough(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result(approval_rate=0.87))
        assert summary.approval_rate == 0.87

    def test_map_visa_no_confidence_field(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result())
        assert not hasattr(summary, "confidence")

    def test_map_visa_error_defaults_none(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result())
        assert summary.error is None

    def test_map_visa_yellow_verdict_uppercase(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result(verdict=Verdict.YELLOW))
        assert summary.verdict == "YELLOW"

    def test_map_visa_red_verdict_uppercase(self):
        from tools.analyze import _map_visa

        summary = _map_visa(_make_visa_result(verdict=Verdict.RED))
        assert summary.verdict == "RED"


# ---------------------------------------------------------------------------
# T-03: Scoring guide
# ---------------------------------------------------------------------------


class TestScoringGuide:
    def test_scoring_guide_has_recommendation_rules(self):
        from tools.analyze import _scoring_guide

        guide = _scoring_guide()
        assert len(guide.recommendation_rules) >= 3
        combined = " ".join(guide.recommendation_rules).upper()
        assert "SKIP" in combined
        assert "APPLY" in combined
        assert "CONSIDER" in combined

    def test_scoring_guide_instructions_mention_score(self):
        from tools.analyze import _scoring_guide

        guide = _scoring_guide()
        assert "score" in guide.instructions.lower()


# ---------------------------------------------------------------------------
# T-04: Orchestration happy path
# ---------------------------------------------------------------------------


class TestAnalyzeJobHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_returns_facts_and_guide(self, monkeypatch):
        from tools import analyze as analyze_mod

        base = _make_resume()
        store = _make_resume_store(base)
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)
        monkeypatch.setattr(
            analyze_mod, "fetch_job_posting", lambda url: _make_job_result()
        )
        monkeypatch.setattr(
            analyze_mod,
            "check_visa_sponsorship",
            lambda company: _make_visa_result(
                verdict=Verdict.GREEN, total_filings=15, approval_rate=0.92
            ),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.job is not None
        assert result.job.title == "Backend Engineer"
        assert result.visa is not None
        assert result.visa.verdict == "GREEN"
        assert result.visa.filings == 15
        assert result.visa.approval_rate == 0.92
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.scoring_guide is not None
        assert len(result.scoring_guide.recommendation_rules) >= 3

    @pytest.mark.asyncio
    async def test_analyze_job_never_raises(self, monkeypatch):
        """analyze_job must NEVER raise — all failures returned in envelope."""
        from tools import analyze as analyze_mod

        store = _make_resume_store(_make_resume())
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)
        monkeypatch.setattr(
            analyze_mod,
            "fetch_job_posting",
            lambda url: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")
        assert result is not None


# ---------------------------------------------------------------------------
# T-05: Failure paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_fetch_fails_stops_orchestration(self, monkeypatch):
        """fetch raises → error='fetch_failed', visa not called."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        store = _make_resume_store(_make_resume())
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)

        visa_spy = MM(side_effect=lambda company: _make_visa_result())
        monkeypatch.setattr(
            analyze_mod,
            "fetch_job_posting",
            lambda url: (_ for _ in ()).throw(ValueError("page not found")),
        )
        monkeypatch.setattr(analyze_mod, "check_visa_sponsorship", visa_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "fetch_failed"
        assert "page not found" in result.message
        assert result.visa is None
        assert result.job is None
        assert result.scoring_guide is None
        visa_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_visa_fails_continues_with_unknown(self, monkeypatch):
        """visa raises → UNKNOWN verdict, envelope still returns job/resume/guide."""
        from tools import analyze as analyze_mod

        store = _make_resume_store(_make_resume())
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)
        monkeypatch.setattr(
            analyze_mod, "fetch_job_posting", lambda url: _make_job_result()
        )
        monkeypatch.setattr(
            analyze_mod,
            "check_visa_sponsorship",
            lambda company: (_ for _ in ()).throw(RuntimeError("USCIS down")),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.visa.verdict == "UNKNOWN"
        assert result.visa.error is not None
        assert result.job is not None
        assert result.resume is not None
        assert result.scoring_guide is not None


# ---------------------------------------------------------------------------
# T-06: Resume precondition — SC-13, SC-14
# ---------------------------------------------------------------------------


class TestResumePrecondition:
    @pytest.mark.asyncio
    async def test_sc14_empty_store_no_resume_error_before_fetch(self, monkeypatch):
        """SC-14: empty resume store → error='no_resume' BEFORE the job fetch."""
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        fetch_spy = MM(side_effect=lambda url: _make_job_result())
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: _make_resume_store())
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", fetch_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "no_resume"
        assert result.message is not None
        assert result.job is None
        assert result.visa is None
        assert result.resume is None
        assert result.scoring_guide is None
        fetch_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_corrupt_store_is_not_reported_as_no_resume(self, monkeypatch):
        """A broken store must not be reported as "you have no resume yet".

        That advice sends the user to save_resume_version, which fails on the
        same file for the same reason. no_resume means the store is readable
        and holds nothing usable; corrupt means the file itself is the problem.
        """
        from tools import analyze as analyze_mod
        from unittest.mock import MagicMock as MM

        fetch_spy = MM(side_effect=lambda url: _make_job_result())
        monkeypatch.setattr(
            analyze_mod,
            "_read_resumes",
            lambda: (_ for _ in ()).throw(
                ValueError("Resume store is corrupt: bad json")
            ),
        )
        monkeypatch.setattr(analyze_mod, "fetch_job_posting", fetch_spy)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "corrupt"
        assert "corrupt" in result.message
        fetch_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_sc13_envelope_carries_base_resume_not_tailored_child(
        self, monkeypatch
    ):
        """SC-13: with a base + a job-tailored child, the envelope carries the
        BASE (root) resume, never the tailored child."""
        from tools import analyze as analyze_mod

        base = _make_resume(
            id="base-1", job_url=None, created_at="2026-01-01T00:00:00+00:00"
        )
        tailored_child = _make_resume(
            id="child-1",
            label="Tailored for Acme",
            parent_id="base-1",
            job_url="https://example.com/job/123",
            created_at="2026-02-01T00:00:00+00:00",
        )
        store = _make_resume_store(base, tailored_child)
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)
        monkeypatch.setattr(
            analyze_mod, "fetch_job_posting", lambda url: _make_job_result()
        )
        monkeypatch.setattr(
            analyze_mod, "check_visa_sponsorship", lambda company: _make_visa_result()
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.resume.id != tailored_child.id


# ---------------------------------------------------------------------------
# T-07: Server registration
# ---------------------------------------------------------------------------


class TestServerRegistration:
    def test_analyze_job_importable(self):
        from tools.analyze import analyze_job

        assert callable(analyze_job)

    def test_analyze_job_registered_in_server(self):
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert "analyze_job" in tool_names

    def test_existing_tools_still_registered(self):
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        for expected in (
            "fetch_job_posting",
            "check_visa_sponsorship",
            "setup_profile",
            "update_profile",
            "get_profile",
        ):
            assert expected in tool_names, f"{expected} missing from tool registry"

    def test_analyze_match_no_longer_registered(self):
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert "analyze_match" not in tool_names

    def test_analyze_job_result_is_pydantic_model(self):
        from pydantic import BaseModel
        from tools.analyze import AnalyzeJobResult

        assert issubclass(AnalyzeJobResult, BaseModel)

"""Tests for tools/analyze.py — analyze_job orchestrator.

PR1 intermediate contract (sqlite-memory-and-pasted-jd, tasks 1.1a-1.1f):
analyze_job no longer fetches a posting or checks visa sponsorship — both
sub-tools are deleted in this same change. The envelope keeps `resume` and
`scoring_guide` only; `job` and `visa` are gone entirely, not merely
unpopulated. This is NOT the final 0.3.0 contract (extracted/work_authorization
land in PR3a) — it is the deliberate PR1 checkpoint shape.

Step 1 (the resume precondition) is UNCHANGED from before this PR — those
tests are ported verbatim to prove that.
"""

from __future__ import annotations

import pytest

from tools.resumes import ResumeStore, ResumeVersion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# T-01: Pydantic models — intermediate contract
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_scoring_guide_fields(self):
        from tools.analyze import ScoringGuide

        guide = ScoringGuide(instructions="do it", recommendation_rules=["a", "b"])
        assert guide.instructions == "do it"
        assert guide.recommendation_rules == ["a", "b"]

    def test_analyze_job_result_all_optional(self):
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert result.resume is None
        assert result.scoring_guide is None
        assert result.error is None
        assert result.message is None

    def test_analyze_job_result_has_no_job_field(self):
        """1.1b: `job` is gone entirely, not merely unpopulated."""
        from tools.analyze import AnalyzeJobResult

        assert "job" not in AnalyzeJobResult.model_fields

    def test_analyze_job_result_has_no_visa_field(self):
        """1.1b: `visa` is gone entirely, not merely unpopulated."""
        from tools.analyze import AnalyzeJobResult

        assert "visa" not in AnalyzeJobResult.model_fields

    def test_job_summary_class_removed(self):
        import tools.analyze as analyze_mod

        assert not hasattr(analyze_mod, "JobSummary")

    def test_visa_summary_class_removed(self):
        import tools.analyze as analyze_mod

        assert not hasattr(analyze_mod, "VisaSummary")

    def test_map_visa_helper_removed(self):
        import tools.analyze as analyze_mod

        assert not hasattr(analyze_mod, "_map_visa")

    def test_analyze_job_result_no_match_or_recommendation_field(self):
        from tools.analyze import AnalyzeJobResult

        result = AnalyzeJobResult()
        assert not hasattr(result, "match"), "Server must NOT compute a match"
        assert not hasattr(result, "recommendation"), (
            "Server must NOT compute a recommendation"
        )


# ---------------------------------------------------------------------------
# T-02: Scoring guide — visa-verdict wording stripped
# ---------------------------------------------------------------------------


class TestScoringGuide:
    def test_scoring_guide_has_recommendation_rules(self):
        from tools.analyze import _scoring_guide

        guide = _scoring_guide()
        assert len(guide.recommendation_rules) >= 1

    def test_scoring_guide_instructions_mention_score(self):
        from tools.analyze import _scoring_guide

        guide = _scoring_guide()
        assert "score" in guide.instructions.lower()

    def test_scoring_guide_no_longer_references_visa(self):
        """1.1e: the visa-verdict clause in the rubric text is now false and
        must be struck — minimal wording fix, not a rubric redesign."""
        from tools.analyze import _scoring_guide

        guide = _scoring_guide()
        combined = " ".join(guide.recommendation_rules).lower()
        assert "visa" not in combined
        assert "visa" not in guide.instructions.lower()


# ---------------------------------------------------------------------------
# T-03: Orchestration happy path (intermediate contract)
# ---------------------------------------------------------------------------


class TestAnalyzeJobHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_returns_resume_and_guide_only(self, monkeypatch):
        from tools import analyze as analyze_mod

        base = _make_resume()
        store = _make_resume_store(base)
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.scoring_guide is not None
        assert not hasattr(result, "job")
        assert not hasattr(result, "visa")

    @pytest.mark.asyncio
    async def test_happy_path_serialized_output_has_no_job_or_visa_key(
        self, monkeypatch
    ):
        """1.1c: no job/visa keys anywhere in the serialized output."""
        from tools import analyze as analyze_mod

        base = _make_resume()
        store = _make_resume_store(base)
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")
        dumped = result.model_dump()
        assert "job" not in dumped
        assert "visa" not in dumped

    @pytest.mark.asyncio
    async def test_analyze_job_never_raises(self, monkeypatch):
        """analyze_job must NEVER raise — all failures returned in envelope."""
        from tools import analyze as analyze_mod

        store = _make_resume_store(_make_resume())
        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: store)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")
        assert result is not None


# ---------------------------------------------------------------------------
# T-04: Resume precondition — UNCHANGED from before this PR (ported verbatim)
# ---------------------------------------------------------------------------


class TestResumePrecondition:
    @pytest.mark.asyncio
    async def test_sc14_empty_store_no_resume_error(self, monkeypatch):
        """SC-14: empty resume store → error='no_resume'."""
        from tools import analyze as analyze_mod

        monkeypatch.setattr(analyze_mod, "_read_resumes", lambda: _make_resume_store())

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "no_resume"
        assert result.message is not None
        assert result.resume is None
        assert result.scoring_guide is None

    @pytest.mark.asyncio
    async def test_corrupt_store_is_not_reported_as_no_resume(self, monkeypatch):
        """A broken store must not be reported as "you have no resume yet".

        That advice sends the user to save_resume_version, which fails on the
        same file for the same reason. no_resume means the store is readable
        and holds nothing usable; corrupt means the file itself is the problem.
        """
        from tools import analyze as analyze_mod

        monkeypatch.setattr(
            analyze_mod,
            "_read_resumes",
            lambda: (_ for _ in ()).throw(
                ValueError("Resume store is corrupt: bad json")
            ),
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "corrupt"
        assert "corrupt" in result.message

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

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.resume.id != tailored_child.id


# ---------------------------------------------------------------------------
# T-05: fetch_failed is unreachable — the path no longer exists
# ---------------------------------------------------------------------------


class TestFetchFailedRemoved:
    def test_fetch_failed_not_a_documented_error(self):
        """1.1: error='fetch_failed' becomes unreachable and must be removed
        from the model's error-vocabulary docstring — dead code left in place
        would misdocument the contract."""
        from tools.analyze import AnalyzeJobResult

        docstring = AnalyzeJobResult.__doc__ or ""
        assert "fetch_failed" not in docstring


# ---------------------------------------------------------------------------
# T-06: Server registration
# ---------------------------------------------------------------------------


class TestServerRegistration:
    def test_analyze_job_importable(self):
        from tools.analyze import analyze_job

        assert callable(analyze_job)

    def test_analyze_job_registered_in_server(self):
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert "analyze_job" in tool_names

    def test_analyze_match_no_longer_registered(self):
        import server

        tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert "analyze_match" not in tool_names

    def test_analyze_job_result_is_pydantic_model(self):
        from pydantic import BaseModel
        from tools.analyze import AnalyzeJobResult

        assert issubclass(AnalyzeJobResult, BaseModel)

    def test_analyze_module_does_not_import_tools_jobs_or_visa(self):
        """1.1a: partial win toward SC-51 — module-level import graph clean."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__)
            .resolve()
            .parent.parent.joinpath("tools", "analyze.py")
            .read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
        assert "tools.jobs" not in imported_modules
        assert "tools.visa" not in imported_modules

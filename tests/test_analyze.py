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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_base_resume(label="Base", content="Jane Doe — Python engineer"):
    from tools.resumes import save_resume_version

    return save_resume_version(content=content, label=label, parent_id=None)


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
    async def test_happy_path_returns_resume_and_guide_only(self, db_path):
        base = _save_base_resume()

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.scoring_guide is not None
        assert not hasattr(result, "job")
        assert not hasattr(result, "visa")

    @pytest.mark.asyncio
    async def test_happy_path_serialized_output_has_no_job_or_visa_key(self, db_path):
        """1.1c: no job/visa keys anywhere in the serialized output."""
        _save_base_resume()

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")
        dumped = result.model_dump()
        assert "job" not in dumped
        assert "visa" not in dumped

    @pytest.mark.asyncio
    async def test_analyze_job_never_raises(self, db_path):
        """analyze_job must NEVER raise — all failures returned in envelope."""
        _save_base_resume()

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")
        assert result is not None


# ---------------------------------------------------------------------------
# T-04: Resume precondition — Guard 2 (no_resume vs corrupt), tasks 2.5o/2.5p.
# The distinction is UNCHANGED in observable behavior from before this PR;
# what changed is the call shape underneath (SQLite, collapsed single
# try/except around url->job_id resolution + _general_resume).
# ---------------------------------------------------------------------------


class TestResumePrecondition:
    @pytest.mark.asyncio
    async def test_sc14_empty_store_no_resume_error(self, db_path):
        """SC-14: empty resume store -> error='no_resume'."""
        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "no_resume"
        assert result.message is not None
        assert result.resume is None
        assert result.scoring_guide is None

    @pytest.mark.asyncio
    async def test_corrupt_store_is_not_reported_as_no_resume(
        self, db_path, monkeypatch
    ):
        """Guard 2 (task 2.5o): a broken database must not be reported as
        "you have no resume yet". That advice sends the user to
        save_resume_version, which fails on the same database for the same
        reason. no_resume means the database is readable and holds nothing
        usable; corrupt means the database itself is the problem.
        """
        import tools.analyze as analyze_mod

        def _boom(job_id=None):
            raise ValueError("Resume store is corrupt: bad data")

        monkeypatch.setattr(analyze_mod, "_general_resume", _boom)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "corrupt"
        assert "corrupt" in result.message

    @pytest.mark.asyncio
    async def test_corrupt_job_lookup_also_reported_as_corrupt_not_no_resume(
        self, db_path, monkeypatch
    ):
        """Guard 2: the url->job_id resolution call is inside the same
        try/except as _general_resume — a corrupt database surfacing there
        must ALSO be reported as corrupt, not no_resume."""
        import tools.analyze as analyze_mod

        def _boom(url):
            raise ValueError("Jobs store is corrupt: bad data")

        monkeypatch.setattr(analyze_mod, "_find_job_id_by_url", _boom)

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error == "corrupt"
        assert "corrupt" in result.message

    @pytest.mark.asyncio
    async def test_sc13_envelope_carries_general_resume_not_tailored_child(
        self, db_path
    ):
        """SC-13: with a base + a job-tailored child, the envelope carries
        the GENERAL resume, never the tailored child."""
        from tools.jobs_store import save_job_analysis
        from tools.resumes import save_resume_version

        job = save_job_analysis(
            url="https://example.com/job/123", title="X", company="Y", country="Z"
        )
        base = _save_base_resume()
        tailored_child = save_resume_version(
            content="tailored",
            label="Tailored for Acme",
            parent_id=base.id,
            job_id=job.id,
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://example.com/job/123")

        assert result.error is None
        assert result.resume is not None
        assert result.resume.id == base.id
        assert result.resume.id != tailored_child.id


# ---------------------------------------------------------------------------
# Guard 1, chain-wide constraint (tasks 2.5m/2.5n) — the anti-self-scoring
# guard must stay green through analyze_job's intermediate (PR1-shaped) call
# site, not deferred to PR3a.
# ---------------------------------------------------------------------------


class TestGuard1AntiSelfScoring:
    @pytest.mark.asyncio
    async def test_2_5m_general_resume_fallback_refuses_a_resume_tailored_to_this_job(
        self, db_path
    ):
        """2.5m: equivalent of test_general_resume_fallback_refuses_a_resume_
        tailored_to_this_job, exercised through analyze_job's intermediate
        call site. The store contains NOTHING untailored — the only version
        is tailored for the exact job being analyzed — so the correct
        outcome is no_resume, never handing back the tailored version."""
        from tools.jobs_store import save_job_analysis
        from tools.resumes import save_resume_version

        job = save_job_analysis(
            url="https://acme.com/job/1", title="X", company="Y", country="Z"
        )
        save_resume_version(
            content="tailored for acme",
            label="Tailored",
            parent_id=None,
            job_id=job.id,
        )

        from tools.analyze import analyze_job

        result = await analyze_job("https://acme.com/job/1")

        assert result.error == "no_resume"
        assert result.resume is None

    @pytest.mark.asyncio
    async def test_2_5n_unsaved_url_resolves_job_id_none_general_still_excludes_tailored(
        self, db_path
    ):
        """2.5n negative case: a url that matches NO saved job resolves to
        job_id=None — proving the None-only-when-no-match branch, not a
        blanket bypass. The general resume must still be returned, and a
        DIFFERENT job's tailored resume must still be excluded correctly."""
        from tools.jobs_store import save_job_analysis
        from tools.resumes import save_resume_version

        other_job = save_job_analysis(
            url="https://other.com/job/9", title="X", company="Y", country="Z"
        )
        base = _save_base_resume()
        save_resume_version(
            content="tailored for other job",
            label="Tailored for other",
            parent_id=base.id,
            job_id=other_job.id,
        )

        from tools.analyze import analyze_job

        # This url matches no saved job at all.
        result = await analyze_job("https://never-saved.com/job/x")

        assert result.error is None
        assert result.resume.id == base.id


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

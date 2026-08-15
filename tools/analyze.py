"""Orchestrator tool: analyze_job.

PR1 intermediate state (sqlite-memory-and-pasted-jd, target 0.3.0): this is
NOT the final 0.3.0 contract and NOT the pre-0.3.0 contract. `fetch_job_posting`
and `check_visa_sponsorship` are deleted in this same change, so the job-fetch
and visa-check steps are removed from this orchestrator entirely — not
stubbed, not caught-and-ignored. The envelope now carries only the caller's
general resume and the scoring rubric. The final contract (Claude-extracted
`title`/`company`/`country` in, `extracted`/`work_authorization` out, `def`
not `async def`) lands in PR3a once the SQLite-backed stores and the pasted-JD
input contract are in place.

Adds zero new external dependencies — the resume lookup is delegated to
tools/resumes.py.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.resumes import _read_resumes, _general_resume, ResumeVersion


# ---------------------------------------------------------------------------
# Scoring rubric (applied by the conversation-side Claude, not the server)
# ---------------------------------------------------------------------------

_RECOMMENDATION_RULES: list[str] = [
    "SKIP if the match score is below 40.",
    "APPLY if the match score is 70 or higher.",
    "CONSIDER in every other case.",
]

_SCORING_INSTRUCTIONS: str = (
    "The job posting is NOT part of this envelope — the server no longer "
    "fetches it. Score against the posting text present in the conversation; "
    "if you do not have it, ask the user to paste it rather than working from "
    "the URL alone. "
    "Using that posting and the candidate's general resume text, produce: "
    "(1) a technical-fit match score from 0 to 100 based on skills, "
    "experience, and education; (2) matched_skills and missing_skills lists; "
    "(3) a short, factual summary of the fit; and (4) a recommendation of "
    "APPLY, CONSIDER, or SKIP by applying the recommendation_rules in order. "
    "Be factual and objective — do not inflate the score. If you recommend "
    "APPLY or CONSIDER, tailor the resume text for this job and save it with "
    "save_resume_version (parent_id=this resume's id, job_url=this job's "
    "url)."
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ScoringGuide(BaseModel):
    """Instructions + rubric for the conversation-side Claude to score the match."""

    instructions: str
    recommendation_rules: list[str]


class AnalyzeJobResult(BaseModel):
    """Decision-ready envelope of FACTS. Claude derives the score and verdict.

    On success, resume/scoring_guide are populated. The server does not
    compute a match score or recommendation — those are left to Claude, which
    reasons over this envelope and the scoring_guide.

    PR1 intermediate shape: no `job` or `visa` field. Both the deleted
    job-fetch and visa-check steps are gone from this orchestrator entirely.
    """

    resume: ResumeVersion | None = None
    scoring_guide: ScoringGuide | None = None
    # top-level error code: no_resume | corrupt
    error: str | None = None
    message: str | None = None  # human-readable explanation for top-level errors


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _scoring_guide() -> ScoringGuide:
    """Build the scoring guide handed to Claude."""
    return ScoringGuide(
        instructions=_SCORING_INSTRUCTIONS,
        recommendation_rules=list(_RECOMMENDATION_RULES),
    )


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------


async def analyze_job(url: str) -> AnalyzeJobResult:
    """Gather the general resume + scoring guide so Claude can score the match.

    1. Verifies a general resume exists (returns error envelope if not).
    2. Returns the general resume and a scoring guide.

    The match score and APPLY/CONSIDER/SKIP recommendation are NOT computed by
    this tool — after calling it, score the candidate's resume against the job
    and apply the scoring_guide's recommendation_rules in your reply.

    The resume injected here is the GENERAL resume (design D6), not the most
    recently tailored one: scoring a job against a resume already tailored
    FOR that job would inflate the match score by scoring the resume against
    itself.

    This tool NEVER raises — all failures are encoded in the return envelope.

    Args:
        url: The raw job posting URL being analyzed. Used only to exclude a
             resume already tailored to this job from the general-resume
             selection.

    Returns:
        AnalyzeJobResult with resume and scoring_guide populated on success,
        or error/message fields populated on failure.
    """
    # --- Step 1: Resume precondition ---
    try:
        store = _read_resumes()
        resume = _general_resume(store, for_job_url=url)
    except ValueError as exc:
        # NOT no_resume: the store exists and is unreadable or malformed.
        # Telling the user to run save_resume_version here sends them to a
        # tool that will fail the same way, on the same file.
        return AnalyzeJobResult(error="corrupt", message=str(exc))
    if resume is None:
        return AnalyzeJobResult(
            error="no_resume",
            message="No resume found. Run save_resume_version first.",
        )

    # --- Step 2: Hand facts + rubric to Claude for scoring ---
    return AnalyzeJobResult(
        resume=resume,
        scoring_guide=_scoring_guide(),
    )

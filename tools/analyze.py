"""Orchestrator tool: analyze_job.

Combines fetch_job_posting → check_visa_sponsorship → profile load into a
single envelope, then hands the data to the conversation-side Claude to score
and recommend. The server performs NO LLM reasoning (no MCP sampling): it
gathers the facts (job, visa history, stored profile) and the scoring rubric,
and Claude produces the match score and APPLY/CONSIDER/SKIP verdict from them.

Adds zero new external dependencies — all I/O is delegated to the sub-tools.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.jobs import fetch_job_posting, JobPostingResult
from tools.visa import check_visa_sponsorship, VisaResult
from tools.profile import _read_profile, ProfileData


# ---------------------------------------------------------------------------
# Scoring rubric (applied by the conversation-side Claude, not the server)
# ---------------------------------------------------------------------------

_RECOMMENDATION_RULES: list[str] = [
    "SKIP if the visa verdict is RED or the match score is below 40 "
    "(SKIP takes precedence over APPLY).",
    "APPLY if the visa verdict is GREEN and the match score is 70 or higher.",
    "CONSIDER in every other case (including UNKNOWN or YELLOW visa verdicts).",
]

_SCORING_INSTRUCTIONS: str = (
    "Using the job posting, the candidate profile, and the visa verdict above, "
    "produce: (1) a technical-fit match score from 0 to 100 based on skills, "
    "experience, and education; (2) matched_skills and missing_skills lists; "
    "(3) a short, factual summary of the fit; and (4) a recommendation of "
    "APPLY, CONSIDER, or SKIP by applying the recommendation_rules in order. "
    "Be factual and objective — do not inflate the score."
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    title: str
    company: str
    url: str


class VisaSummary(BaseModel):
    verdict: str  # "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    filings: int
    approval_rate: float
    error: str | None = None


class ScoringGuide(BaseModel):
    """Instructions + rubric for the conversation-side Claude to score the match."""

    instructions: str
    recommendation_rules: list[str]


class AnalyzeJobResult(BaseModel):
    """Decision-ready envelope of FACTS. Claude derives the score and verdict.

    On success, job/visa/profile/scoring_guide are populated. The server does
    not compute a match score or recommendation — those are left to Claude,
    which reasons over this envelope and the scoring_guide.
    """

    job: JobSummary | None = None
    visa: VisaSummary | None = None
    profile: ProfileData | None = None
    scoring_guide: ScoringGuide | None = None
    error: str | None = None  # top-level error code: no_profile | fetch_failed
    message: str | None = None  # human-readable explanation for top-level errors


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _map_visa(visa_result: VisaResult, error: str | None = None) -> VisaSummary:
    """Map a VisaResult to a VisaSummary for the envelope.

    - verdict is uppercased (Verdict enum value → uppercase string)
    - filings ← total_filings
    - approval_rate passes through directly (no 'confidence' field)
    """
    return VisaSummary(
        verdict=visa_result.verdict.value.upper(),
        filings=visa_result.total_filings,
        approval_rate=visa_result.approval_rate,
        error=error,
    )


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
    """Gather job + visa + profile for a posting so Claude can score the match.

    Performs the data-gathering half of a job analysis in one call:
    1. Verifies a profile exists (returns error envelope if not).
    2. Fetches the job posting from the given URL.
    3. Checks H-1B visa sponsorship history for the company.
    4. Returns the job, visa verdict, stored profile, and a scoring guide.

    The match score and APPLY/CONSIDER/SKIP recommendation are NOT computed by
    this tool — after calling it, score the candidate profile against the job
    and apply the scoring_guide's recommendation_rules in your reply.

    This tool NEVER raises — all failures are encoded in the return envelope.

    Args:
        url: The raw job posting URL to analyze.

    Returns:
        AnalyzeJobResult with job, visa, profile, and scoring_guide populated
        on success, or error/message fields populated on failure.
    """
    # --- Step 1: Profile precondition (BEFORE any fetch) ---
    try:
        profile = _read_profile()
        if profile is None:
            return AnalyzeJobResult(
                error="no_profile",
                message="No profile found. Run setup_profile first.",
            )
    except (FileNotFoundError, ValueError):
        return AnalyzeJobResult(
            error="no_profile",
            message="No profile found. Run setup_profile first.",
        )

    # --- Step 2: Fetch job posting ---
    try:
        job_result: JobPostingResult = fetch_job_posting(url)
    except Exception as exc:
        return AnalyzeJobResult(
            error="fetch_failed",
            message=str(exc),
        )

    job_summary = JobSummary(
        title=job_result.title,
        company=job_result.company,
        url=job_result.source_url or url,
    )

    # --- Step 3: Visa check (failure → UNKNOWN, orchestration CONTINUES) ---
    visa_summary: VisaSummary
    try:
        visa_result: VisaResult = check_visa_sponsorship(job_result.company)
        visa_summary = _map_visa(visa_result)
    except Exception as exc:
        visa_summary = VisaSummary(
            verdict="UNKNOWN",
            filings=0,
            approval_rate=0.0,
            error=str(exc),
        )

    # --- Step 4: Hand facts + rubric to Claude for scoring ---
    return AnalyzeJobResult(
        job=job_summary,
        visa=visa_summary,
        profile=profile,
        scoring_guide=_scoring_guide(),
    )

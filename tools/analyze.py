"""Orchestrator tool: analyze_job.

Combines fetch_job_posting → check_visa_sponsorship → analyze_match into a
single decision-ready envelope with a derived recommendation. Adds zero new
external dependencies — all I/O is delegated to the three sub-tools.
"""

from __future__ import annotations

from pydantic import BaseModel
from mcp.server.fastmcp import Context

from tools.jobs import fetch_job_posting, JobPostingResult
from tools.visa import check_visa_sponsorship, VisaResult
from tools.match import analyze_match, MatchResult
from tools.profile import _read_profile


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    title: str
    company: str
    url: str


class VisaSummary(BaseModel):
    verdict: str          # "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    filings: int
    approval_rate: float
    error: str | None = None


class MatchSummary(BaseModel):
    score: int | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    summary: str | None = None
    error: str | None = None


class AnalyzeJobResult(BaseModel):
    job: JobSummary | None = None
    visa: VisaSummary | None = None
    match: MatchSummary | None = None
    recommendation: str | None = None   # "APPLY" | "CONSIDER" | "SKIP" | None
    error: str | None = None            # top-level error code: no_profile | fetch_failed
    message: str | None = None          # human-readable explanation for top-level errors


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _compute_recommendation(verdict: str, score: int | None) -> str | None:
    """Derive a deterministic recommendation from visa verdict and match score.

    Precedence rules (in order):
    1. score is None → None (match failed, no recommendation possible)
    2. verdict == "RED" OR score < 40 → "SKIP"  (SKIP beats APPLY)
    3. verdict == "GREEN" AND score >= 70 → "APPLY"
    4. everything else (incl. UNKNOWN/YELLOW) → "CONSIDER"
    """
    if score is None:
        return None
    if verdict == "RED" or score < 40:
        return "SKIP"
    if verdict == "GREEN" and score >= 70:
        return "APPLY"
    return "CONSIDER"


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


def _map_match(match_result: MatchResult) -> MatchSummary:
    """Map a MatchResult to a MatchSummary for the envelope."""
    return MatchSummary(
        score=match_result.score,
        matched_skills=match_result.matched_skills,
        missing_skills=match_result.missing_skills,
        summary=match_result.summary,
        error=match_result.error_message if not match_result.success else None,
    )


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------


async def analyze_job(url: str, ctx: Context) -> AnalyzeJobResult:
    """Orchestrate job analysis: fetch → visa check → match → recommendation.

    Performs a full job analysis in one call:
    1. Verifies a profile exists (returns error envelope if not).
    2. Fetches the job posting from the given URL.
    3. Checks H-1B visa sponsorship history for the company.
    4. Scores the job against the stored profile.
    5. Derives a deterministic recommendation (APPLY / CONSIDER / SKIP).

    This tool NEVER raises — all failures are encoded in the return envelope.

    Args:
        url: The raw job posting URL to analyze.
        ctx: Injected by FastMCP — forwarded to analyze_match for sampling.

    Returns:
        AnalyzeJobResult with job, visa, match, and recommendation populated
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

    # --- Step 4: Match analysis (failure → null score + null recommendation) ---
    match_summary: MatchSummary
    try:
        match_result: MatchResult = await analyze_match(job_result, ctx)
        match_summary = _map_match(match_result)
    except Exception as exc:
        match_summary = MatchSummary(
            score=None,
            error=str(exc),
        )

    # --- Step 5: Derive recommendation ---
    recommendation = _compute_recommendation(visa_summary.verdict, match_summary.score)

    return AnalyzeJobResult(
        job=job_summary,
        visa=visa_summary,
        match=match_summary,
        recommendation=recommendation,
    )

"""Job store persistence tools: save_job_analysis, list_jobs, mark_applied.

The conversation-side Claude analyzes a job posting and produces a score and
recommendation. These tools PERSIST and RETRIEVE that structured data — they
never call back to the model. This keeps the server free of MCP sampling and
matches the project's philosophy: tools shape data, Claude reasons.

Job records are stored at ~/.config/runway-mcp/jobs.json.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from tools import _storage

# ---------------------------------------------------------------------------
# ISO-8601 parsing helper
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 datetime string to a timezone-aware datetime.

    Handles both 'Z' and '+00:00' suffixes so that lexicographic format
    differences do not cause incorrect timestamp comparisons.

    Args:
        value: ISO-8601 string (e.g. '2025-06-01T12:00:00Z' or
               '2025-06-01T12:00:00+00:00').

    Returns:
        An aware datetime object.

    Raises:
        ValueError: if the string cannot be parsed as ISO-8601.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_JOBS_PATH: Path = Path.home() / ".config" / "runway-mcp" / "jobs.json"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class StoredJob(BaseModel):
    """A single analyzed job record persisted to the jobs store."""

    url: str  # dedup / upsert key
    title: str
    company: str
    visa_verdict: str  # GREEN | YELLOW | RED | UNKNOWN
    analyzed_at: str  # ISO-8601, server-stamped by save_job_analysis
    applied: bool = False
    score: int | None = None  # 0-100, Claude-supplied (never computed server-side)
    recommendation: str | None = None  # APPLY | CONSIDER | SKIP | None, Claude-supplied
    notes: str | None = None


class JobStore(BaseModel):
    """Top-level container for the jobs.json file."""

    jobs: list[StoredJob] = []


class SaveJobResult(BaseModel):
    """Return value for save_job_analysis."""

    success: bool
    url: str | None = None
    updated: bool | None = None  # True=upserted existing record, False=new record
    storage_path: str | None = None
    error_message: str | None = None


class ListJobsResult(BaseModel):
    """Return value for list_jobs."""

    success: bool
    jobs: list[StoredJob] = []
    count: int = 0
    error_message: str | None = None


class MarkAppliedResult(BaseModel):
    """Return value for mark_applied."""

    success: bool
    url: str | None = None
    error: str | None = None  # "not_found" | "corrupt"
    message: str | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _read_jobs(path: Path | None = None) -> JobStore:
    """Read and parse the stored jobs JSON.

    Args:
        path: Path to the jobs JSON file. If None, uses the module-level
              _JOBS_PATH (resolved at call time so tests can monkeypatch it).

    Returns:
        JobStore parsed from the file, or an empty JobStore when the file does
        not exist (first-run / normal state — missing is NOT an error).

    Raises:
        ValueError: if the file exists but its content is malformed JSON or
                    fails pydantic validation. The file is never auto-repaired.
    """
    resolved = path if path is not None else _JOBS_PATH
    if not resolved.exists():
        return JobStore(jobs=[])
    try:
        return JobStore.model_validate_json(resolved.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Jobs store is corrupt: {exc}") from exc


def _write_jobs(store: JobStore, path: Path | None = None) -> None:
    """Atomically write the jobs store as pretty-printed JSON.

    Delegates to tools._storage.atomic_write_json (temp-file + rename
    pattern) to avoid corrupting an existing store on partial write.

    Args:
        store: The JobStore to persist.
        path:  Destination path. If None, uses _JOBS_PATH (resolved at call
               time so tests can monkeypatch it).
    """
    resolved = path if path is not None else _JOBS_PATH
    _storage.atomic_write_json(store, resolved, tmp_prefix=".jobs_tmp_")


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def save_job_analysis(
    url: str,
    title: str,
    company: str,
    visa_verdict: str,
    score: int | None = None,
    recommendation: str | None = None,
    notes: str | None = None,
) -> SaveJobResult:
    """Persist an analyzed job record to the jobs store.

    The server stamps ``analyzed_at`` with the current UTC time. Upserts by
    ``url`` — if a record with the same URL already exists it is replaced in
    full (latest save wins). This tool NEVER raises — all failures are encoded
    in the return envelope.

    Args:
        url:            The job posting URL (used as the dedup / upsert key).
        title:          Job title extracted from the posting.
        company:        Company name extracted from the posting.
        visa_verdict:   H-1B visa verdict for this company (GREEN/YELLOW/RED/UNKNOWN).
        score:          0-100 match score produced by Claude (None if not scored yet).
        recommendation: APPLY/CONSIDER/SKIP produced by Claude (None if not yet scored).
        notes:          Optional free-text notes from the user.

    Returns:
        SaveJobResult with success=True, url, updated flag, and storage_path on
        success; success=False with error_message on failure.
    """
    try:
        store = _read_jobs()
    except ValueError as exc:
        return SaveJobResult(success=False, error_message=str(exc))

    # Detect upsert
    existing_index = next((i for i, j in enumerate(store.jobs) if j.url == url), None)
    updated = existing_index is not None

    try:
        # FIX 3: preserve `applied` on upsert; default False for new records
        existing_applied = store.jobs[existing_index].applied if updated else False

        # Build the new record (server stamps analyzed_at)
        # FIX 2: StoredJob construction is inside the try so ValidationError is caught
        new_record = StoredJob(
            url=url,
            title=title,
            company=company,
            visa_verdict=visa_verdict,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            applied=existing_applied,
            score=score,
            recommendation=recommendation,
            notes=notes,
        )

        if updated:
            store.jobs[existing_index] = new_record
        else:
            store.jobs.append(new_record)

        _write_jobs(store)
    except (ValueError, ValidationError) as exc:
        return SaveJobResult(success=False, error_message=str(exc))

    return SaveJobResult(
        success=True,
        url=url,
        updated=updated,
        storage_path=str(_JOBS_PATH.resolve()),
    )


def list_jobs(
    since: str | None = None,
    applied: bool | None = None,
    min_score: int | None = None,
    limit: int | None = None,
    sort_by: str = "analyzed_at",
) -> ListJobsResult:
    """Return stored job records with optional filtering, sorting, and limiting.

    Pipeline order: FILTER → SORT → LIMIT. This tool NEVER raises.

    Args:
        since:     ISO-8601 string cutoff (inclusive). Only jobs where
                   analyzed_at >= since are returned (lexicographic compare).
        applied:   If True, return only applied jobs. If False, return only
                   non-applied jobs. If None (default), no filter applied.
        min_score: Minimum score threshold (inclusive). Jobs with score=None
                   are excluded when this filter is active.
        limit:     Maximum number of records to return (after sort).
        sort_by:   "analyzed_at" (default, newest first) or "score"
                   (descending, None scores last).

    Returns:
        ListJobsResult with success=True and filtered/sorted records on
        success; success=False with error_message on failure.
    """
    try:
        store = _read_jobs()
    except ValueError as exc:
        return ListJobsResult(success=False, error_message=str(exc))

    items = list(store.jobs)

    # --- FILTER ---
    if since is not None:
        try:
            since_dt = _parse_iso(since)
            # Parse each stored timestamp too — a malformed record must surface
            # as an error envelope, never raise to the MCP boundary.
            items = [j for j in items if _parse_iso(j.analyzed_at) >= since_dt]
        except ValueError as exc:
            return ListJobsResult(
                success=False,
                error_message=f"Invalid 'since' timestamp or corrupt record timestamp: {exc}",
            )
    if applied is not None:
        items = [j for j in items if j.applied == applied]
    if min_score is not None:
        items = [j for j in items if j.score is not None and j.score >= min_score]

    # --- SORT ---
    if sort_by == "analyzed_at":
        items = sorted(items, key=lambda j: j.analyzed_at, reverse=True)
    elif sort_by == "score":
        # Tuple key: (score is not None, score or 0) with reverse=True
        # → True (1) sorts before False (0) under reverse → scored items first
        # → among scored items, higher score wins
        items = sorted(
            items,
            key=lambda j: (j.score is not None, j.score if j.score is not None else 0),
            reverse=True,
        )
    else:
        return ListJobsResult(
            success=False,
            error_message=f"Invalid sort_by: {sort_by!r} (use 'analyzed_at' or 'score')",
        )

    # --- LIMIT ---
    if limit is not None:
        if limit <= 0:
            return ListJobsResult(
                success=False,
                error_message="limit must be a positive integer",
            )
        items = items[:limit]

    return ListJobsResult(success=True, jobs=items, count=len(items))


def mark_applied(url: str, notes: str | None = None) -> MarkAppliedResult:
    """Mark a job record as applied.

    Sets ``applied=True`` on the record matching ``url``. If ``notes`` is
    provided, updates the notes field; otherwise leaves it unchanged.
    Returns success=False with error="not_found" when no record matches.
    This tool NEVER raises.

    Args:
        url:   The job posting URL identifying the record to update.
        notes: Optional notes to attach (replaces existing notes if provided).

    Returns:
        MarkAppliedResult with success=True and the url on success;
        success=False with error="not_found" when no record matches;
        success=False with error/message on store read/write failure.
    """
    try:
        store = _read_jobs()
    except ValueError as exc:
        return MarkAppliedResult(success=False, error="corrupt", message=str(exc))

    index = next((i for i, j in enumerate(store.jobs) if j.url == url), None)
    if index is None:
        return MarkAppliedResult(success=False, error="not_found")

    record = store.jobs[index]
    updated_record = record.model_copy(
        update={
            "applied": True,
            "notes": notes if notes is not None else record.notes,
        }
    )
    store.jobs[index] = updated_record

    try:
        _write_jobs(store)
    except Exception as exc:
        return MarkAppliedResult(success=False, error="write_error", message=str(exc))

    return MarkAppliedResult(success=True, url=url, message="Marked as applied.")

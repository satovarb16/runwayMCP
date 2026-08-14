"""Job store persistence tools: save_job_analysis, list_jobs, mark_applied.

The conversation-side Claude analyzes a job posting and produces a score and
recommendation. These tools PERSIST and RETRIEVE that structured data — they
never call back to the model. This keeps the server free of MCP sampling and
matches the project's philosophy: tools shape data, Claude reasons.

Job records are stored at ~/.config/runway-mcp/jobs.json.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from enum import Enum
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

_SCHEMA_VERSION: int = 2  # v1 == implicit legacy schema (`applied: bool`, no key)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ApplicationStatus(str, Enum):
    """The 7 allowed application status values (binding, spec-defined)."""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


class StoredJob(BaseModel):
    """A single analyzed job record persisted to the jobs store."""

    url: str  # dedup / upsert key
    title: str
    company: str
    visa_verdict: str  # GREEN | YELLOW | RED | UNKNOWN
    analyzed_at: str  # ISO-8601, server-stamped by save_job_analysis
    status: ApplicationStatus = ApplicationStatus.NOT_APPLIED
    score: int | None = None  # 0-100, Claude-supplied (never computed server-side)
    recommendation: str | None = None  # APPLY | CONSIDER | SKIP | None, Claude-supplied
    notes: str | None = None


class JobStore(BaseModel):
    """Top-level container for the jobs.json file."""

    schema_version: int = _SCHEMA_VERSION
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


def _coerce_legacy(raw: dict) -> tuple[dict, bool]:
    """Coerce a pre-migration jobs payload to the current schema, in memory only.

    For every job record that does not already carry a ``status`` key, the
    legacy ``applied: bool`` field is renamed: ``True`` -> ``"applied"``,
    ``False``/missing -> ``"not_applied"`` (SC-25, SC-26). The payload is then
    stamped with ``schema_version = 2``. A payload already carrying
    ``schema_version == 2`` whose records all have ``status`` is returned
    unchanged (SC-27).

    The per-record ``status`` check — not the top-level version stamp — is what
    decides whether coercion runs. A file stamped version 2 that still holds a
    legacy record (hand-edited, or written by external tooling) would otherwise
    have its ``applied`` value silently dropped by pydantic as an unknown field
    and default to ``not_applied``. The per-record guard is idempotent and
    cheap, so it costs nothing to always run it.

    Args:
        raw: The parsed JSON payload (dict) read from the jobs store file.

    Returns:
        A tuple of ``(coerced_payload, was_legacy)`` where ``was_legacy`` is
        True when the payload was not already at the current schema (used to
        trigger a one-time backup before any legacy read).

    Raises:
        ValueError: if ``jobs`` is not a list, or any record is not an object.
                    Raised rather than skipped so a malformed store surfaces as
                    corrupt instead of silently losing records.
    """
    jobs = raw.get("jobs", [])
    if not isinstance(jobs, list):
        raise ValueError(f"expected 'jobs' to be a list, got {type(jobs).__name__}")

    coerced_jobs = []
    did_coerce = False
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError(
                f"expected each job record to be an object, got {type(job).__name__}"
            )
        job = dict(job)
        if "status" not in job:
            applied = job.pop("applied", False)
            job["status"] = "applied" if applied else "not_applied"
            did_coerce = True
        coerced_jobs.append(job)

    was_legacy = did_coerce or raw.get("schema_version") != _SCHEMA_VERSION
    if not was_legacy:
        return raw, False

    coerced = dict(raw)
    coerced["jobs"] = coerced_jobs
    coerced["schema_version"] = _SCHEMA_VERSION
    return coerced, True


def _backup_once(path: Path) -> None:
    """Write a one-time ``.bak`` copy of a legacy jobs store before coercion.

    Best-effort: any OSError while copying is swallowed with a stderr warning
    so a failed backup never blocks the read path. Skips silently if the
    backup already exists (forward-only after the first legacy read).

    Args:
        path: Path to the jobs store file being backed up.
    """
    backup_path = path.with_name(path.name + ".bak")
    if backup_path.exists():
        return
    try:
        shutil.copyfile(path, backup_path)
    except OSError as exc:
        print(f"Warning: failed to write jobs store backup: {exc}", file=sys.stderr)


def _read_jobs(path: Path | None = None) -> JobStore:
    """Read and parse the stored jobs JSON.

    Pre-validation-coerces legacy (pre-migration) records via
    `_coerce_legacy` before handing the payload to pydantic, so old
    `applied: bool` records load cleanly as `status` (SC-25..SC-27). When a
    legacy payload is detected, a one-time `.bak` backup is written first
    (best-effort, see `_backup_once`) before coercion is applied in memory.

    Args:
        path: Path to the jobs JSON file. If None, uses the module-level
              _JOBS_PATH (resolved at call time so tests can monkeypatch it).

    Returns:
        JobStore parsed from the file, or an empty JobStore when the file does
        not exist (first-run / normal state — missing is NOT an error).

    Raises:
        ValueError: if the file exists but its content is malformed JSON or
                    fails pydantic validation (even after coercion). The file
                    is never auto-repaired.
    """
    resolved = path if path is not None else _JOBS_PATH
    if not resolved.exists():
        return JobStore(jobs=[])
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(
                f"expected a JSON object at the top level, got {type(raw).__name__}"
            )
        coerced, was_legacy = _coerce_legacy(raw)
        if was_legacy:
            _backup_once(resolved)
        return JobStore.model_validate(coerced)
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
        # FIX 3: preserve `status` on upsert; default NOT_APPLIED for new records
        existing_status = (
            store.jobs[existing_index].status
            if updated
            else ApplicationStatus.NOT_APPLIED
        )

        # Build the new record (server stamps analyzed_at)
        # FIX 2: StoredJob construction is inside the try so ValidationError is caught
        new_record = StoredJob(
            url=url,
            title=title,
            company=company,
            visa_verdict=visa_verdict,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            status=existing_status,
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
        applied:   If True, return only jobs whose status is "applied". If
                   False, return only jobs whose status is NOT "applied"
                   (any other status counts as not-applied). If None
                   (default), no filter applied.
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
        # "Applied" means any post-application status, not the literal APPLIED
        # member: a job in interviewing/offer/rejected/withdrawn/ghosted has
        # definitively been applied to. Comparing against APPLIED alone would
        # drop progressed applications from applied=True and resurface them
        # under applied=False. Superseded by the status filter in PR3.
        items = [
            j for j in items if (j.status != ApplicationStatus.NOT_APPLIED) == applied
        ]
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

    Sets ``status=ApplicationStatus.APPLIED`` on the record matching ``url``.
    If ``notes`` is provided, updates the notes field; otherwise leaves it
    unchanged.
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
            "status": ApplicationStatus.APPLIED,
            "notes": notes if notes is not None else record.notes,
        }
    )
    store.jobs[index] = updated_record

    try:
        _write_jobs(store)
    except Exception as exc:
        return MarkAppliedResult(success=False, error="write_error", message=str(exc))

    return MarkAppliedResult(success=True, url=url, message="Marked as applied.")

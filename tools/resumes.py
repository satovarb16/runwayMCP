"""Resume version store: save_resume_version, get_resume_version, list_resume_versions.

The conversation-side Claude drafts and tailors resume text. This module
PERSISTS and RETRIEVES that raw text — it never parses, structures, scores,
or judges resume content, and it never calls back to the model. This is the
project's third persistence shape: unlike tools/profile.py (singleton
overwrite) and tools/jobs_store.py (upsert-by-key), resume versions must
COEXIST, so this store is append-only with parent_id lineage: no code path
here mutates or deletes a saved version.

That is a property of the logic, not a durability guarantee. save_resume_version
does a whole-file read-modify-write, so two saves dispatched close together can
read the same snapshot and the later write drops the earlier one's new version.
atomic_write_json keeps the file from ever being left corrupt, but it cannot
make a lost write reappear. Same hazard as tools/jobs_store.py; acceptable for a
single-user local server, and a shared file lock is the fix if that changes.

Resume versions are stored at ~/.config/runway-mcp/resumes.json.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from tools import _storage
from tools._storage import store_lock

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_RESUMES_PATH: Path = Path.home() / ".config" / "runway-mcp" / "resumes.json"

_SCHEMA_VERSION: int = 1

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ResumeVersion(BaseModel):
    """A single saved resume version. Content is raw text, stored verbatim."""

    # Unknown fields are an error, not something to drop quietly — an
    # append-only store that silently discards fields it does not recognise
    # is not actually preserving history.
    model_config = ConfigDict(extra="forbid")

    id: str  # uuid4().hex, server-generated
    label: str
    content: str  # raw text — sole source of truth, no format/length validation
    # No default: parent_id=None carries the special meaning "this is the
    # base/root" version, so every construction site must decide explicitly
    # rather than silently falling into that meaning (spec: "required, no
    # default (caller must be explicit)").
    parent_id: str | None
    job_url: str | None = None
    created_at: str  # ISO-8601 UTC, server-stamped


class ResumeStore(BaseModel):
    """Top-level container for the resumes.json file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = _SCHEMA_VERSION
    versions: list[ResumeVersion] = []


class SaveResumeVersionResult(BaseModel):
    """Return value for save_resume_version.

    Modeled on jobs_store.SetStatusResult rather than the simpler
    SaveJobResult: save_resume_version has multiple distinct validation
    failure modes (invalid_parent, parent_not_found, write_error, corrupt)
    that a caller needs to branch on programmatically, so it carries a coded
    `error` + human `message` pair instead of a single `error_message` blob.
    """

    success: bool
    id: str | None = None
    label: str | None = None
    parent_id: str | None = None
    storage_path: str | None = None
    error: str | None = (
        # "invalid_parent" | "parent_not_found" | "invalid_input"
        # | "write_error" | "corrupt"
        None
    )
    message: str | None = None


class GetResumeVersionResult(BaseModel):
    """Return value for get_resume_version."""

    success: bool
    version: ResumeVersion | None = None
    error: str | None = None  # "not_found" | "corrupt"
    message: str | None = None


class ResumeVersionSummary(BaseModel):
    """A resume version WITHOUT its content, for listing.

    Every other field of ResumeVersion, minus `content`. Listing returns these
    rather than full records because `content` is an entire resume: returning
    20 of them would push 20 full documents into the model context just to
    answer "which versions do I have?". Fetch the text with
    get_resume_version once the caller knows which version it wants.
    """

    id: str
    label: str
    parent_id: str | None
    job_url: str | None
    created_at: str


class ListResumeVersionsResult(BaseModel):
    """Return value for list_resume_versions."""

    success: bool
    versions: list[ResumeVersionSummary] = []
    count: int = 0
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _read_resumes(path: Path | None = None) -> ResumeStore:
    """Read and parse the stored resumes JSON.

    Args:
        path: Path to the resumes JSON file. If None, uses the module-level
              _RESUMES_PATH (resolved at call time so tests can monkeypatch
              it).

    Returns:
        ResumeStore parsed from the file, or an empty ResumeStore when the
        file does not exist (first-run / normal state — missing is NOT an
        error).

    Raises:
        ValueError: if the file exists but its content is malformed JSON or
                    fails pydantic validation, or if it cannot be read at all
                    (permissions, path is a directory). OSError is wrapped
                    rather than propagated so every caller's existing
                    `except ValueError` keeps the tool boundary
                    exception-free. Unreadable is reported as "unreadable",
                    not "corrupt" — the file may be perfectly valid, just
                    inaccessible. The file is never auto-repaired.
    """
    resolved = path if path is not None else _RESUMES_PATH
    if not resolved.exists():
        return ResumeStore(versions=[])
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(
                f"expected a JSON object at the top level, got {type(raw).__name__}"
            )
        return ResumeStore.model_validate(raw)
    except OSError as exc:
        raise ValueError(f"Resume store is unreadable: {exc}") from exc
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Resume store is corrupt: {exc}") from exc


def _write_resumes(store: ResumeStore, path: Path | None = None) -> None:
    """Atomically write the resumes store as pretty-printed JSON.

    Delegates to tools._storage.atomic_write_json (temp-file + rename
    pattern) to avoid corrupting an existing store on partial write.

    Args:
        store: The ResumeStore to persist.
        path:  Destination path. If None, uses _RESUMES_PATH (resolved at
               call time so tests can monkeypatch it).
    """
    resolved = path if path is not None else _RESUMES_PATH
    _storage.atomic_write_json(store, resolved, tmp_prefix=".resumes_tmp_")


def _general_resume(
    store: ResumeStore, for_job_url: str | None = None
) -> ResumeVersion | None:
    """Select the GENERAL (non-job-tailored) resume for analyze_job (design D6).

    "General" means not written for any particular job, which is exactly
    `job_url is None` — not "the root". Those differ once a user refreshes
    their general resume, and the distinction matters:

    1. Among versions with `job_url is None`, return the most recently
       created one. Usually that is the base itself, but a user who updates
       their general resume must save it as a new version with
       `parent_id=<existing id>` (save_resume_version forbids a second
       `parent_id=None` once a base exists, SC-03/SC-04). Returning the root
       instead would hand analyze_job the original text forever and silently
       ignore every update.
    2. Fallback, reachable only if no stored version has `job_url=None` —
       every version, including the first, was saved against some job:
       return the most recently created root.

    The property both branches must preserve is that the returned resume was
    never written for the job being analyzed — scoring a job against a resume
    already rewritten for that job is scoring it against itself. Branch 1 gets
    that for free. Branch 2 does not: the base is allowed to carry a job_url,
    so `for_job_url` is excluded explicitly there. If that leaves nothing,
    return None and let the caller report no_resume, which is honest, rather
    than hand back a resume known to be tailored to this exact posting.

    With several independent trees (roots sharing no common ancestor, e.g.
    the user started an unrelated second base), recency alone decides. That
    keeps this a single total order over created_at instead of requiring a
    notion of "the active tree", which neither spec nor design defines.

    Caller contract: a version written FOR a job must be saved with that
    job_url. Nothing server-side can enforce it — requiring job_url whenever
    parent_id is set would forbid the general-resume refresh above, which is
    the same shape. A tailored version saved without a job_url becomes the
    newest untailored one and is returned as "general" until corrected. That
    is recoverable with the same tool and no file surgery: save the real
    general text again with job_url=None and it wins on recency.

    Args:
        store:       The ResumeStore to select from.
        for_job_url: The job URL about to be analyzed, so branch 2 can refuse
                     a resume tailored to it. None disables that exclusion.

    Returns:
        The selected ResumeVersion, or None when nothing usable exists
        (analyze_job maps None to error="no_resume").
    """
    general_candidates = [v for v in store.versions if v.job_url is None]
    if general_candidates:
        return max(general_candidates, key=lambda v: v.created_at)

    root_candidates = [
        v
        for v in store.versions
        if v.parent_id is None and (for_job_url is None or v.job_url != for_job_url)
    ]
    if root_candidates:
        return max(root_candidates, key=lambda v: v.created_at)

    return None


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def save_resume_version(
    content: str,
    label: str,
    parent_id: str | None = None,
    job_url: str | None = None,
) -> SaveResumeVersionResult:
    """Append a new resume version to the store. Never mutates or deletes.

    The store enforces a single-root tree: the first saved version's
    parent_id MUST be None (the base/general resume); every subsequent
    version's parent_id MUST reference an existing version id. Content is
    stored raw and verbatim — this tool performs no length, format, or "fits
    on one page" validation (that judgment belongs to Claude, in the
    conversation). This tool NEVER raises.

    Args:
        content:   Raw resume text, stored verbatim.
        label:     Caller-supplied human-readable label (e.g. "Base",
                   "Tailored for Acme").
        parent_id: id of the version this one derives from. None only on the
                   very first save (establishes the base). Must reference an
                   existing version id otherwise.
        job_url:   Optional job posting URL this version was tailored for.

    Returns:
        SaveResumeVersionResult with success=True, id, label, parent_id, and
        storage_path on success; success=False with error/message on
        failure ("invalid_parent" when the parent_id/empty-store combination
        is illegal, "parent_not_found" when parent_id does not exist,
        "corrupt" on a store read failure, "write_error" on a store write
        failure).
    """

    with store_lock(_RESUMES_PATH):
        try:
            store = _read_resumes()
        except ValueError as exc:
            return SaveResumeVersionResult(
                success=False, error="corrupt", message=str(exc)
            )

        if not store.versions:
            if parent_id is not None:
                return SaveResumeVersionResult(
                    success=False,
                    error="invalid_parent",
                    message=(
                        "The store is empty; the first saved version must have "
                        "parent_id=None."
                    ),
                )
        else:
            if parent_id is None:
                return SaveResumeVersionResult(
                    success=False,
                    error="invalid_parent",
                    message=(
                        "A base version already exists; parent_id is required "
                        "for subsequent versions."
                    ),
                )
            if not any(v.id == parent_id for v in store.versions):
                return SaveResumeVersionResult(
                    success=False,
                    error="parent_not_found",
                    message=f"No resume version exists with id {parent_id!r}.",
                )

        # Build the record OUTSIDE the write try: a bad content/label type is a
        # caller input error, and reporting it as "write_error" would send a
        # caller branching on the error codes into a retry loop instead of fixing
        # its arguments.
        try:
            record = ResumeVersion(
                id=uuid.uuid4().hex,
                label=label,
                content=content,
                parent_id=parent_id,
                job_url=job_url,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        except ValidationError as exc:
            return SaveResumeVersionResult(
                success=False, error="invalid_input", message=str(exc)
            )

        try:
            store.versions.append(record)
            _write_resumes(store)
        except Exception as exc:
            return SaveResumeVersionResult(
                success=False, error="write_error", message=str(exc)
            )

        return SaveResumeVersionResult(
            success=True,
            id=record.id,
            label=record.label,
            parent_id=record.parent_id,
            storage_path=str(_RESUMES_PATH.resolve()),
        )


def get_resume_version(id: str) -> GetResumeVersionResult:
    """Retrieve a single resume version by id, or the most recent via "latest".

    This tool NEVER raises.

    Args:
        id: Exact version id, or the literal string "latest" to return the
            most recently created version in the store (by created_at — not
            necessarily the base/root).

    Returns:
        GetResumeVersionResult with success=True and version on success;
        success=False with error="not_found" when no version matches id (or
        the store is empty), or error="corrupt" on a store read failure.
    """
    try:
        store = _read_resumes()
    except ValueError as exc:
        return GetResumeVersionResult(success=False, error="corrupt", message=str(exc))

    if id == "latest":
        if not store.versions:
            return GetResumeVersionResult(
                success=False,
                error="not_found",
                message="No resume versions have been saved yet.",
            )
        version = max(store.versions, key=lambda v: v.created_at)
        return GetResumeVersionResult(success=True, version=version)

    version = next((v for v in store.versions if v.id == id), None)
    if version is None:
        return GetResumeVersionResult(
            success=False,
            error="not_found",
            message=f"No resume version exists with id {id!r}.",
        )
    return GetResumeVersionResult(success=True, version=version)


def list_resume_versions(
    job_url: str | None = None,
    limit: int | None = None,
) -> ListResumeVersionsResult:
    """Return stored resume versions, newest-first, with optional filtering.

    Pipeline order: FILTER -> SORT -> LIMIT, mirroring list_jobs. This tool
    NEVER raises.

    Args:
        job_url: When provided, only versions tailored for this exact job URL
                  are returned. None (default) returns versions regardless of
                  job_url.
        limit:    Maximum number of records to return (after sort). Must be a
                  positive integer if provided.

    Returns:
        ListResumeVersionsResult with success=True and filtered/sorted
        SUMMARIES on success (no resume text — call get_resume_version for
        that); success=False with error_message on failure.
    """
    try:
        store = _read_resumes()
    except ValueError as exc:
        return ListResumeVersionsResult(success=False, error_message=str(exc))

    items = list(store.versions)

    if job_url is not None:
        items = [v for v in items if v.job_url == job_url]

    items = sorted(items, key=lambda v: v.created_at, reverse=True)

    if limit is not None:
        if limit <= 0:
            return ListResumeVersionsResult(
                success=False, error_message="limit must be a positive integer"
            )
        items = items[:limit]

    summaries = [
        ResumeVersionSummary(
            id=v.id,
            label=v.label,
            parent_id=v.parent_id,
            job_url=v.job_url,
            created_at=v.created_at,
        )
        for v in items
    ]
    return ListResumeVersionsResult(
        success=True, versions=summaries, count=len(summaries)
    )

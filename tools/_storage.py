"""Shared atomic JSON persistence helper.

Extracted from the tempfile-mkstemp + os.replace pattern duplicated across
the store modules (jobs_store.py and profile.py both hand-rolled it). This
module owns filesystem I/O only — kept separate from pure string-normalization
concerns (formerly tools/_utils.py, deleted in 0.3.0 PR1 along with its only
consumers).

profile.py was deleted in full in 0.3.0 PR1 — the read-only migration hatch
it carried (get_profile) is gone with it.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock
from pydantic import BaseModel

# Seconds to wait for another holder before giving up. Generous: the critical
# section is a small JSON read-modify-write, so anything approaching this
# means a stuck or crashed holder, not contention.
_LOCK_TIMEOUT = 10.0


@contextmanager
def store_lock(path: Path) -> Iterator[None]:
    """Serialize a whole read-modify-write cycle on ``path``.

    atomic_write_json guarantees the file is never left half-written. It does
    NOT prevent a lost update: every store tool reads the whole file, edits it
    in memory, and writes it back, so two overlapping calls read the same
    snapshot and the later write silently discards the earlier one's change.
    No error, no corruption — just a status update that quietly did not happen.

    Wrap the ENTIRE cycle, not just the write. Locking only the write is
    useless, because by then both callers already hold the stale snapshot.

    This is a file lock rather than a threading.Lock because the racing
    parties are not always threads: FastMCP dispatches these sync tools on a
    worker pool, but two MCP hosts (say Claude Desktop and Claude Code) each
    spawn their own server process against the same store. An in-process lock
    would leave that case silently unprotected.

    Read-only paths (list_jobs, get_resume_version) do not need this: the
    atomic rename means a reader sees either the whole old file or the whole
    new one, never a mix.

    Args:
        path: The store file being guarded. The lock itself lives beside it
              as ``<path>.lock``.

    Raises:
        filelock.Timeout: if the lock cannot be acquired within the timeout.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{path}.lock", timeout=_LOCK_TIMEOUT):
        yield


def atomic_write_json(payload: BaseModel, path: Path, *, tmp_prefix: str) -> None:
    """Atomically write a pydantic model as pretty-printed JSON.

    Creates parent directories if they do not exist. Writes to a temp file in
    the destination directory, flushes and fsyncs it, then renames it over
    ``path``, so a partial write never corrupts an existing file at ``path``.

    The fsync is what makes that true rather than merely likely: ``Path.replace``
    makes the directory-entry swap atomic, but the new file's bytes can still be
    sitting in the OS write-back cache, and a crash in that window publishes a
    truncated or zero-length file. Note this syncs the file, not the containing
    directory — on a crash the rename itself may still be lost, leaving the
    previous complete version in place, which is the safe direction to fail.

    Args:
        payload:    The pydantic model to persist (serialized via
                    ``model_dump_json(indent=2)``).
        path:       Destination path.
        tmp_prefix: Prefix for the temp file created alongside ``path``
                    (e.g. ``.jobs_tmp_``). Keyword-only so call sites are
                    self-documenting and callers cannot accidentally
                    positionally swap it with ``path``.

    Raises:
        Exception: any error raised while serializing or writing re-raises
                   after the temp file is unlinked (best-effort cleanup).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=tmp_prefix, suffix=".json"
    )
    tmp_path = Path(tmp_path_str)

    # open() only takes ownership of the fd once it succeeds. If it raises, the
    # fd is still ours: leaking it would also keep a handle on the temp file,
    # which on Windows makes the cleanup unlink fail and replaces the original
    # exception with a confusing PermissionError.
    try:
        handle = open(tmp_fd, "w", encoding="utf-8")
    except Exception:
        os.close(tmp_fd)
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        with handle as f:
            f.write(payload.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

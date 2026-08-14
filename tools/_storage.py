"""Shared atomic JSON persistence helper.

Extracted from the tempfile-mkstemp + os.replace pattern duplicated across
the store modules (jobs_store.py and profile.py both hand-rolled it). This
module owns filesystem I/O only — it is intentionally separate from
tools/_utils.py, which stays pure string normalization with zero I/O.

profile.py is NOT migrated to this helper — it still has its own inline
copy of the pattern. It is deleted wholesale in a later change
(resume-tailoring-and-status-tracking, PR6) rather than updated, so
migrating it here would be wasted work.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel


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

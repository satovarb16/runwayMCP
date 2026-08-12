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

import tempfile
from pathlib import Path

from pydantic import BaseModel


def atomic_write_json(payload: BaseModel, path: Path, *, tmp_prefix: str) -> None:
    """Atomically write a pydantic model as pretty-printed JSON.

    Creates parent directories if they do not exist. Uses a temp-file +
    rename pattern (tempfile.mkstemp + Path.replace) so a partial write never
    corrupts an existing file at ``path``.

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
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(payload.model_dump_json(indent=2))
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

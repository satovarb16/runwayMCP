"""Tests for tools/_storage.py — shared atomic JSON write helper.

Mirrors the atomic-write test coverage previously duplicated inside
test_jobs_store.py / test_profile_tools.py, now exercised directly against
the extracted tools._storage.atomic_write_json helper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel


class _DummyModel(BaseModel):
    """Minimal pydantic model used to exercise atomic_write_json generically."""

    name: str
    value: int = 0


def test_atomic_write_json_creates_parent_dirs(tmp_path):
    """atomic_write_json creates parent directories if they do not exist."""
    from tools._storage import atomic_write_json

    deep_path = tmp_path / "a" / "b" / "c" / "data.json"
    payload = _DummyModel(name="x")

    atomic_write_json(payload, deep_path, tmp_prefix=".dummy_tmp_")

    assert deep_path.exists()


def test_atomic_write_json_writes_valid_json(tmp_path):
    """atomic_write_json produces parseable, pretty-printed JSON matching the model."""
    from tools._storage import atomic_write_json

    path = tmp_path / "data.json"
    payload = _DummyModel(name="hello", value=42)

    atomic_write_json(payload, path, tmp_prefix=".dummy_tmp_")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"name": "hello", "value": 42}


def test_atomic_write_json_round_trips(tmp_path):
    """A model written via atomic_write_json can be read back via model_validate_json."""
    from tools._storage import atomic_write_json

    path = tmp_path / "data.json"
    payload = _DummyModel(name="round-trip", value=7)

    atomic_write_json(payload, path, tmp_prefix=".dummy_tmp_")

    result = _DummyModel.model_validate_json(path.read_text(encoding="utf-8"))
    assert result == payload


def test_atomic_write_json_overwrites_existing(tmp_path):
    """atomic_write_json replaces an existing file's content rather than appending."""
    from tools._storage import atomic_write_json

    path = tmp_path / "data.json"
    atomic_write_json(_DummyModel(name="first"), path, tmp_prefix=".dummy_tmp_")
    atomic_write_json(_DummyModel(name="second"), path, tmp_prefix=".dummy_tmp_")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["name"] == "second"


def test_atomic_write_json_uses_given_tmp_prefix(tmp_path, monkeypatch):
    """The temp file created during the write uses the caller-supplied prefix."""
    from tools._storage import atomic_write_json

    path = tmp_path / "data.json"
    seen_tmp_names = []

    original_replace = Path.replace

    def spying_replace(self, target):
        seen_tmp_names.append(self.name)
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", spying_replace)

    atomic_write_json(_DummyModel(name="x"), path, tmp_prefix=".custom_prefix_")

    assert len(seen_tmp_names) == 1
    assert seen_tmp_names[0].startswith(".custom_prefix_")
    assert seen_tmp_names[0].endswith(".json")


def test_atomic_write_json_cleans_temp_on_error(tmp_path):
    """On write failure, the temp file is unlinked and the original file is intact."""
    from tools._storage import atomic_write_json

    path = tmp_path / "data.json"
    original_content = json.dumps({"name": "keep-me", "value": 1})
    path.write_text(original_content, encoding="utf-8")

    class BrokenModel(_DummyModel):
        def model_dump_json(self, **kwargs):
            raise RuntimeError("simulated write failure")

    broken = BrokenModel(name="broken")

    with pytest.raises(RuntimeError, match="simulated write failure"):
        atomic_write_json(broken, path, tmp_prefix=".dummy_tmp_")

    # Original file is still intact
    assert path.read_text(encoding="utf-8") == original_content
    # No orphan temp files
    temp_files = list(tmp_path.glob(".dummy_tmp_*"))
    assert len(temp_files) == 0, f"Orphan temp files found: {temp_files}"


def test_atomic_write_json_fsyncs_before_rename(tmp_path, monkeypatch):
    """Data must reach the disk before the rename publishes the new file.

    Path.replace makes the directory entry swap atomic, but without an fsync
    the file's bytes may still be in the OS write-back cache. A crash between
    the rename and writeback leaves a truncated or zero-length store — the
    exact corruption this helper exists to prevent.
    """
    import os
    import tools._storage as storage_mod
    from tools._storage import atomic_write_json

    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(
        storage_mod.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1]
    )

    path = tmp_path / "data.json"
    atomic_write_json(_DummyModel(name="durable"), path, tmp_prefix=".dummy_tmp_")

    assert synced, "atomic_write_json completed without fsyncing the temp file"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "durable"


def test_atomic_write_json_closes_fd_when_open_fails(tmp_path, monkeypatch):
    """A failure opening the temp fd must not leak it or mask the real error.

    open() takes ownership of the fd only once it succeeds. If it raises, the
    fd is still ours; leaving it open leaks a descriptor and, on Windows,
    makes the cleanup unlink fail and replace the original exception.
    """
    import os
    import tools._storage as storage_mod
    from tools._storage import atomic_write_json

    closed: list[int] = []
    real_close = os.close
    monkeypatch.setattr(
        storage_mod.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1]
    )
    monkeypatch.setattr(
        storage_mod,
        "open",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
        raising=False,
    )

    path = tmp_path / "data.json"
    with pytest.raises(OSError, match="boom"):
        atomic_write_json(_DummyModel(name="x"), path, tmp_prefix=".dummy_tmp_")

    assert closed, "temp fd was leaked when open() failed"
    assert list(tmp_path.glob(".dummy_tmp_*")) == []


def test_store_lock_serializes_read_modify_write(tmp_path, monkeypatch):
    """Two concurrent read-modify-write cycles must not lose an update.

    Without the lock both threads read the same snapshot, each adds its own
    entry, and whichever writes last wins — the other entry is gone. No
    corruption, no error, just a silently missing record. atomic_write_json
    prevents a half-written file; it cannot prevent a lost write.
    """
    import json
    import threading
    from tools._storage import store_lock

    path = tmp_path / "counter.json"
    path.write_text(json.dumps({"entries": []}), encoding="utf-8")
    start = threading.Barrier(2)

    def append(name: str) -> None:
        start.wait()  # maximize overlap
        with store_lock(path):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["entries"].append(name)
            # widen the read-modify-write window so an unlocked version
            # would reliably lose one entry rather than flakily
            threading.Event().wait(0.05)
            path.write_text(json.dumps(data), encoding="utf-8")

    threads = [threading.Thread(target=append, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert sorted(entries) == ["a", "b"], f"lost an update: {entries}"


def test_store_lock_is_reentrant_across_sequential_calls(tmp_path):
    """Acquiring and releasing repeatedly must not deadlock or leak."""
    from tools._storage import store_lock

    path = tmp_path / "seq.json"
    for _ in range(3):
        with store_lock(path):
            pass

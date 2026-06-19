"""Tests for persistence helpers in tools.profile."""

from __future__ import annotations

import json
import pytest


# ---------------------------------------------------------------------------
# _write_profile
# ---------------------------------------------------------------------------


def test_write_profile_creates_parent_dirs(tmp_path):
    from tools.profile import _write_profile, ProfileData

    nested = tmp_path / "a" / "b" / "profile.json"
    profile = ProfileData(name="Jane")
    _write_profile(profile, nested)
    assert nested.exists()


def test_write_profile_writes_valid_json(tmp_path):
    from tools.profile import _write_profile, ProfileData

    dest = tmp_path / "profile.json"
    profile = ProfileData(name="Jane", skills=["Python"])
    _write_profile(profile, dest)

    raw = json.loads(dest.read_text(encoding="utf-8"))
    assert raw["name"] == "Jane"
    assert raw["skills"] == ["Python"]


def test_write_profile_readable_as_profile_data(tmp_path):
    from tools.profile import _write_profile, ProfileData

    dest = tmp_path / "profile.json"
    original = ProfileData(
        name="Jane",
        skills=["Python"],
        experience=[],
        education=[],
    )
    _write_profile(original, dest)

    loaded = ProfileData.model_validate_json(dest.read_text(encoding="utf-8"))
    assert loaded.name == original.name
    assert loaded.skills == original.skills


def test_write_profile_overwrites_existing(tmp_path):
    from tools.profile import _write_profile, ProfileData

    dest = tmp_path / "profile.json"
    _write_profile(ProfileData(name="Old"), dest)
    _write_profile(ProfileData(name="New"), dest)

    loaded = ProfileData.model_validate_json(dest.read_text(encoding="utf-8"))
    assert loaded.name == "New"


def test_write_profile_no_partial_file_left_on_failure(tmp_path, monkeypatch):
    """Simulate a write failure and verify no corrupted temp file remains."""
    from tools.profile import ProfileData
    import tools.profile as profile_mod

    dest = tmp_path / "profile.json"
    dest.write_text('{"name": "Original"}', encoding="utf-8")

    # Monkeypatch model_dump_json to raise mid-write
    def boom(self, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(ProfileData, "model_dump_json", boom)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        profile_mod._write_profile(ProfileData(name="New"), dest)

    # Original must be untouched
    assert json.loads(dest.read_text(encoding="utf-8"))["name"] == "Original"

    # No temp file should remain
    temp_files = list(dest.parent.glob(".profile_tmp_*.json"))
    assert temp_files == []

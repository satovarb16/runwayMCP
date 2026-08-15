"""Tests for tools.profile: the read-only get_profile migration hatch.

setup_profile and update_profile were removed in this change (SC-17) —
resumes are now version-tracked via tools.resumes (save_resume_version).
get_profile remains unchanged, read-only, for one release so Claude can
read a legacy profile.json and re-submit it as text via save_resume_version.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_SAMPLE_PROFILE_DICT = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "location": "NYC",
    "skills": ["Python", "Go"],
    "experience": [{"company": "Acme", "title": "SWE", "duration_years": 2.5}],
    "education": [{"institution": "MIT", "degree": "BSc", "field": "CS", "year": 2018}],
    "languages": ["English"],
    "summary": "Engineer.",
}
_SAMPLE_PROFILE_JSON = json.dumps(_SAMPLE_PROFILE_DICT)


def _patch_profile_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _PROFILE_PATH to a temp location."""
    import tools.profile as profile_mod

    new_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", new_path)
    return new_path


# ---------------------------------------------------------------------------
# SC-17: setup_profile / update_profile no longer exist
# ---------------------------------------------------------------------------


def test_setup_profile_not_importable():
    """SC-17: setup_profile no longer exists on tools.profile."""
    import tools.profile as profile_mod

    assert not hasattr(profile_mod, "setup_profile")


def test_update_profile_not_importable():
    """SC-17: update_profile no longer exists on tools.profile."""
    import tools.profile as profile_mod

    assert not hasattr(profile_mod, "update_profile")


def test_setup_profile_not_registered_on_server():
    """SC-17: setup_profile is not a registered MCP tool."""
    import server

    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert "setup_profile" not in tool_names


def test_update_profile_not_registered_on_server():
    """SC-17: update_profile is not a registered MCP tool."""
    import server

    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert "update_profile" not in tool_names


def test_get_profile_still_registered_on_server():
    """get_profile survives as the read-only migration hatch."""
    import server

    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert "get_profile" in tool_names


# ---------------------------------------------------------------------------
# SC-15: get_profile — legacy file present, behavior unchanged
# ---------------------------------------------------------------------------


def test_get_profile_success(tmp_path, monkeypatch):
    from tools.profile import get_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")

    result = get_profile()

    assert result.success is True
    assert result.profile is not None
    assert result.profile.name == "Jane Doe"
    assert result.profile.skills == ["Python", "Go"]
    assert result.error is None


# ---------------------------------------------------------------------------
# SC-16: get_profile — no legacy file, unchanged no_profile error
# ---------------------------------------------------------------------------


def test_get_profile_no_profile(tmp_path, monkeypatch):
    from tools.profile import get_profile

    _patch_profile_path(monkeypatch, tmp_path)  # do not write a file

    result = get_profile()

    assert result.success is False
    assert result.profile is None
    assert result.error == "no_profile"
    assert result.message is not None


def test_get_profile_corrupt(tmp_path, monkeypatch):
    from tools.profile import get_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.write_text("{corrupt json!!}", encoding="utf-8")

    result = get_profile()

    assert result.success is False
    assert result.error == "corrupt"
    assert result.message is not None


# ---------------------------------------------------------------------------
# _read_profile — still used internally by get_profile
# ---------------------------------------------------------------------------


def test_read_profile_success(tmp_path, monkeypatch):
    """_read_profile returns a ProfileData when the file exists and is valid."""
    import tools.profile as profile_mod
    from tools.profile import _read_profile, ProfileData

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", profile_path)

    result = _read_profile(path=profile_path)

    assert isinstance(result, ProfileData)
    assert result.name == "Jane Doe"
    assert result.skills == ["Python", "Go"]


def test_read_profile_missing(tmp_path, monkeypatch):
    """_read_profile raises ValueError when the profile file does not exist."""
    import tools.profile as profile_mod
    from tools.profile import _read_profile

    missing_path = tmp_path / "no_profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", missing_path)

    with pytest.raises(ValueError, match="(?i)no profile|not found"):
        _read_profile(path=missing_path)


def test_read_profile_corrupt(tmp_path, monkeypatch):
    """_read_profile raises ValueError when the file contains invalid JSON."""
    import tools.profile as profile_mod
    from tools.profile import _read_profile

    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{corrupt json!!}", encoding="utf-8")
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", profile_path)

    with pytest.raises(ValueError, match="(?i)corrupt|parse|invalid"):
        _read_profile(path=profile_path)


def test_get_profile_missing_is_never_labelled_corrupt(tmp_path, monkeypatch):
    """A missing profile is `no_profile`, never `corrupt`, even under a race.

    get_profile used to call exists() and then read. If the file vanished in
    between — and the module docstring now tells users to delete it by hand —
    the missing file came back as error="corrupt" with the message
    "No profile found.", an envelope contradicting itself.
    """
    import tools.profile as profile_mod
    from tools.profile import get_profile, ProfileNotFound

    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(
        profile_mod,
        "_read_profile",
        lambda *a, **k: (_ for _ in ()).throw(ProfileNotFound("No profile found.")),
    )

    result = get_profile()

    assert result.error == "no_profile"
    assert "corrupt" not in (result.message or "")


def test_get_profile_missing_points_at_the_replacement_tool(tmp_path, monkeypatch):
    """The migration hatch must name where to go, not just say no."""
    import tools.profile as profile_mod
    from tools.profile import get_profile

    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", tmp_path / "nope.json")

    result = get_profile()

    assert result.error == "no_profile"
    assert "save_resume_version" in result.message

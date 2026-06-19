"""Integration tests for _persist, setup_profile, update_profile, get_profile.

Option A: the conversation-side Claude extracts the profile from the CV and
passes structured ProfileData to these tools, which only persist/retrieve it.
No MCP sampling, no CV file reading on the server.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_profile():
    from tools.profile import ProfileData

    return ProfileData.model_validate(_SAMPLE_PROFILE_DICT)


def _patch_profile_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _PROFILE_PATH to a temp location."""
    import tools.profile as profile_mod

    new_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", new_path)
    return new_path


# ---------------------------------------------------------------------------
# _persist — overwrite=False, no existing profile → success
# ---------------------------------------------------------------------------


def test_persist_no_existing_profile_creates_file(tmp_path, monkeypatch):
    from tools.profile import _persist

    profile_path = _patch_profile_path(monkeypatch, tmp_path)

    result = _persist(_make_profile(), overwrite=False)

    assert result.success is True
    assert profile_path.exists()
    assert result.profile_summary["name"] == "Jane Doe"
    assert result.profile_summary["skills_count"] == 2
    assert result.profile_summary["experience_years"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# _persist — overwrite=False + profile exists → ValueError
# ---------------------------------------------------------------------------


def test_persist_overwrite_false_profile_exists_raises(tmp_path, monkeypatch):
    from tools.profile import _persist

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    with pytest.raises(ValueError, match="(?i)use update_profile"):
        _persist(_make_profile(), overwrite=False)


# ---------------------------------------------------------------------------
# _persist — overwrite=True + profile exists → overwrites
# ---------------------------------------------------------------------------


def test_persist_overwrite_true_replaces_existing(tmp_path, monkeypatch):
    from tools.profile import _persist

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    result = _persist(_make_profile(), overwrite=True)

    assert result.success is True
    loaded = json.loads(profile_path.read_text(encoding="utf-8"))
    assert loaded["name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# setup_profile — success path (via public API)
# ---------------------------------------------------------------------------


def test_setup_profile_success(tmp_path, monkeypatch):
    from tools.profile import setup_profile

    _patch_profile_path(monkeypatch, tmp_path)

    result = setup_profile(_make_profile())

    assert result.success is True
    assert result.profile_summary is not None
    assert result.error_message is None


# ---------------------------------------------------------------------------
# setup_profile — profile already exists → success=False (no exception)
# ---------------------------------------------------------------------------


def test_setup_profile_already_exists_returns_failure(tmp_path, monkeypatch):
    from tools.profile import setup_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    result = setup_profile(_make_profile())

    assert result.success is False
    assert "update_profile" in result.error_message


# ---------------------------------------------------------------------------
# update_profile — overwrites existing profile
# ---------------------------------------------------------------------------


def test_update_profile_overwrites(tmp_path, monkeypatch):
    from tools.profile import update_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    result = update_profile(_make_profile())

    assert result.success is True
    loaded = json.loads(profile_path.read_text(encoding="utf-8"))
    assert loaded["name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# update_profile — no existing profile → creates one
# ---------------------------------------------------------------------------


def test_update_profile_creates_when_missing(tmp_path, monkeypatch):
    from tools.profile import update_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)

    result = update_profile(_make_profile())

    assert result.success is True
    assert profile_path.exists()


# ---------------------------------------------------------------------------
# get_profile
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


def test_get_profile_no_profile(tmp_path, monkeypatch):
    from tools.profile import get_profile

    _patch_profile_path(monkeypatch, tmp_path)  # do not write a file

    result = get_profile()

    assert result.success is False
    assert result.profile is None
    assert result.error == "no_profile"
    assert "setup_profile" in result.message


def test_get_profile_corrupt(tmp_path, monkeypatch):
    from tools.profile import get_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.write_text("{corrupt json!!}", encoding="utf-8")

    result = get_profile()

    assert result.success is False
    assert result.error == "corrupt"
    assert result.message is not None


# ---------------------------------------------------------------------------
# _read_profile
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

    with pytest.raises(ValueError, match="(?i)no profile|not found|setup_profile"):
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


# ---------------------------------------------------------------------------
# load_profile — public contract
# ---------------------------------------------------------------------------


def test_load_profile_success(tmp_path, monkeypatch):
    """load_profile returns a ProfileData when the file exists and is valid."""
    import tools.profile as profile_mod
    from tools.profile import load_profile, ProfileData

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(_SAMPLE_PROFILE_JSON, encoding="utf-8")
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", profile_path)

    result = load_profile(path=profile_path)

    assert isinstance(result, ProfileData)
    assert result.name == "Jane Doe"
    assert result.skills == ["Python", "Go"]


def test_load_profile_missing(tmp_path, monkeypatch):
    """load_profile raises ValueError when the profile file does not exist."""
    import tools.profile as profile_mod
    from tools.profile import load_profile

    missing_path = tmp_path / "no_profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", missing_path)

    with pytest.raises(ValueError, match="(?i)no profile|not found|setup_profile"):
        load_profile(path=missing_path)


def test_load_profile_corrupt(tmp_path, monkeypatch):
    """load_profile raises ValueError when the file contains invalid JSON."""
    import tools.profile as profile_mod
    from tools.profile import load_profile

    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{corrupt json!!}", encoding="utf-8")
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", profile_path)

    with pytest.raises(ValueError, match="(?i)corrupt|parse|invalid"):
        load_profile(path=profile_path)

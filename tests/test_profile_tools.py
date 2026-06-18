"""Integration tests for _ingest, setup_profile, update_profile (Phase 3 — TDD RED/GREEN)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from mcp.types import TextContent, CreateMessageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PROFILE_JSON = json.dumps(
    {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "location": "NYC",
        "skills": ["Python", "Go"],
        "experience": [{"company": "Acme", "title": "SWE", "duration_years": 2.5}],
        "education": [
            {"institution": "MIT", "degree": "BSc", "field": "CS", "year": 2018}
        ],
        "languages": ["English"],
        "summary": "Engineer.",
    }
)


def _make_ctx(response_json: str = _SAMPLE_PROFILE_JSON) -> MagicMock:
    """Return a fake Context whose session.create_message returns the given JSON."""
    ctx = MagicMock()
    result = MagicMock(spec=CreateMessageResult)
    result.content = TextContent(type="text", text=response_json)
    ctx.session.create_message = AsyncMock(return_value=result)
    return ctx


def _patch_profile_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect _PROFILE_PATH to a temp location."""
    import tools.profile as profile_mod

    new_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_mod, "_PROFILE_PATH", new_path)
    return new_path


# ---------------------------------------------------------------------------
# _ingest — overwrite=False, no existing profile → success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_no_existing_profile_creates_file(tmp_path, monkeypatch):
    from tools.profile import _ingest

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await _ingest(str(cv), ctx, overwrite=False)

    assert result.success is True
    assert profile_path.exists()
    assert result.profile_summary["name"] == "Jane Doe"
    assert result.profile_summary["skills_count"] == 2
    assert result.profile_summary["experience_years"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# _ingest — overwrite=False + profile exists → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_overwrite_false_profile_exists_raises(tmp_path, monkeypatch):
    from tools.profile import _ingest

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    with pytest.raises(ValueError, match="(?i)use update_profile"):
        await _ingest(str(cv), ctx, overwrite=False)


# ---------------------------------------------------------------------------
# _ingest — overwrite=True + profile exists → overwrites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_overwrite_true_replaces_existing(tmp_path, monkeypatch):
    from tools.profile import _ingest

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await _ingest(str(cv), ctx, overwrite=True)

    assert result.success is True
    loaded = json.loads(profile_path.read_text(encoding="utf-8"))
    assert loaded["name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# _ingest — sample returns malformed JSON → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_malformed_sample_response_raises(tmp_path, monkeypatch):
    from tools.profile import _ingest

    _patch_profile_path(monkeypatch, tmp_path)
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx(response_json="{not valid json}")
    with pytest.raises(ValueError, match="malformed JSON|could not parse"):
        await _ingest(str(cv), ctx, overwrite=False)


# ---------------------------------------------------------------------------
# setup_profile — success path (via public API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_profile_success(tmp_path, monkeypatch):
    from tools.profile import setup_profile

    _patch_profile_path(monkeypatch, tmp_path)
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await setup_profile(str(cv), ctx)

    assert result.success is True
    assert result.profile_summary is not None
    assert result.error_message is None


# ---------------------------------------------------------------------------
# setup_profile — profile already exists → success=False (no exception)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_profile_already_exists_returns_failure(tmp_path, monkeypatch):
    from tools.profile import setup_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await setup_profile(str(cv), ctx)

    assert result.success is False
    assert "update_profile" in result.error_message


# ---------------------------------------------------------------------------
# update_profile — overwrites existing profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_profile_overwrites(tmp_path, monkeypatch):
    from tools.profile import update_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text('{"name": "Old"}', encoding="utf-8")

    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await update_profile(str(cv), ctx)

    assert result.success is True
    loaded = json.loads(profile_path.read_text(encoding="utf-8"))
    assert loaded["name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# update_profile — no existing profile → creates one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_profile_creates_when_missing(tmp_path, monkeypatch):
    from tools.profile import update_profile

    profile_path = _patch_profile_path(monkeypatch, tmp_path)
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF fake content")

    ctx = _make_ctx()
    result = await update_profile(str(cv), ctx)

    assert result.success is True
    assert profile_path.exists()


# ---------------------------------------------------------------------------
# _read_profile — TDD RED (Phase 1.1)
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
# load_profile — public contract (TDD RED)
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

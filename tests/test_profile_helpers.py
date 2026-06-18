"""Tests for private helper functions in tools.profile (Phase 2 — TDD RED/GREEN)."""

from __future__ import annotations

import json
import pytest


# ---------------------------------------------------------------------------
# _read_cv_bytes
# ---------------------------------------------------------------------------


def test_read_cv_bytes_missing_file(tmp_path):
    from tools.profile import _read_cv_bytes

    with pytest.raises(ValueError, match="file not found"):
        _read_cv_bytes(str(tmp_path / "nonexistent.pdf"))


def test_read_cv_bytes_unsupported_extension(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.txt"
    p.write_bytes(b"hello")
    with pytest.raises(ValueError, match="unsupported file format"):
        _read_cv_bytes(str(p))


def test_read_cv_bytes_empty_file(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.pdf"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        _read_cv_bytes(str(p))


def test_read_cv_bytes_too_large(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.pdf"
    # 5 MB + 1 byte
    p.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        _read_cv_bytes(str(p))


def test_read_cv_bytes_valid_pdf(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.pdf"
    p.write_bytes(b"%PDF-1.4 fake content")
    raw, ext = _read_cv_bytes(str(p))
    assert raw == b"%PDF-1.4 fake content"
    assert ext == ".pdf"


def test_read_cv_bytes_valid_docx(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.docx"
    p.write_bytes(b"PK fake docx content")
    raw, ext = _read_cv_bytes(str(p))
    assert ext == ".docx"


def test_read_cv_bytes_extension_case_insensitive(tmp_path):
    from tools.profile import _read_cv_bytes

    p = tmp_path / "resume.PDF"
    p.write_bytes(b"%PDF content")
    raw, ext = _read_cv_bytes(str(p))
    assert ext == ".pdf"


# ---------------------------------------------------------------------------
# _parse_sampled_json
# ---------------------------------------------------------------------------

_VALID_JSON = json.dumps(
    {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "location": "NYC",
        "skills": ["Python", "Go"],
        "experience": [{"company": "Acme", "title": "SWE"}],
        "education": [{"institution": "MIT"}],
        "languages": ["English"],
        "summary": "Engineer.",
    }
)


def test_parse_sampled_json_clean():
    from tools.profile import _parse_sampled_json, ProfileData

    result = _parse_sampled_json(_VALID_JSON)
    assert isinstance(result, ProfileData)
    assert result.name == "Jane Doe"
    assert "Python" in result.skills


def test_parse_sampled_json_with_json_fence():
    from tools.profile import _parse_sampled_json

    fenced = f"```json\n{_VALID_JSON}\n```"
    result = _parse_sampled_json(fenced)
    assert result.name == "Jane Doe"


def test_parse_sampled_json_with_plain_fence():
    from tools.profile import _parse_sampled_json

    fenced = f"```\n{_VALID_JSON}\n```"
    result = _parse_sampled_json(fenced)
    assert result.name == "Jane Doe"


def test_parse_sampled_json_malformed():
    from tools.profile import _parse_sampled_json

    with pytest.raises(ValueError, match="malformed JSON|could not parse"):
        _parse_sampled_json("{not valid json")


def test_parse_sampled_json_schema_invalid():
    from tools.profile import _parse_sampled_json

    # skills should be a list, not a string
    bad = json.dumps(
        {
            "name": "X",
            "skills": "not-a-list",
            "experience": [],
            "education": [],
        }
    )
    with pytest.raises(ValueError, match="could not parse"):
        _parse_sampled_json(bad)


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

import json
from pathlib import Path

import pytest

import server

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"


@pytest.mark.integration
def test_analyze_job_registered():
    """analyze_job must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "analyze_job" in registered_names


@pytest.mark.integration
def test_analyze_match_not_registered():
    """analyze_match was removed in Option A (a pre-0.2.0 refactor); its
    replacement, get_profile, was itself removed in 0.3.0 with no direct
    substitute — see tests/test_removed_tools.py."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "analyze_match" not in registered_names


@pytest.mark.integration
def test_jobs_store_tools_registered():
    """save_job_analysis, list_jobs, set_application_status must be registered
    in the FastMCP instance; mark_applied must be absent (SC-30)."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "save_job_analysis" in registered_names
    assert "list_jobs" in registered_names
    assert "set_application_status" in registered_names
    assert "mark_applied" not in registered_names


@pytest.mark.integration
def test_resume_tools_registered():
    """save_resume_version, get_resume_version, list_resume_versions must be
    registered in the FastMCP instance (PR4, Phase B)."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "save_resume_version" in registered_names
    assert "get_resume_version" in registered_names
    assert "list_resume_versions" in registered_names


@pytest.mark.integration
def test_manifest_tools_match_registered_tools():
    """manifest.json's tools[] must name exactly the tools registered on the
    FastMCP server — derived from both sides so this test keeps working as
    tools are added/removed, rather than hardcoding a literal name list that
    would just move the staleness into the test."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())

    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_names = {tool["name"] for tool in manifest["tools"]}

    assert manifest_names == registered_names

    stale = {
        "mark_applied",
        "setup_profile",
        "update_profile",
        "fetch_job_posting",
        "check_visa_sponsorship",
        "get_profile",
    }
    assert not (stale & registered_names)
    assert not (stale & manifest_names)


@pytest.mark.integration
def test_manifest_list_jobs_description_mentions_company_filter():
    """Finding 6: list_jobs gained a `company` filter (D9, PR3a) but its
    manifest.json description still only enumerated status/score/date — the
    set-equality test above only checks tool NAMES, so nothing else catches
    a stale description."""
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = {tool["name"]: tool for tool in manifest["tools"]}

    assert "company" in entries["list_jobs"]["description"]

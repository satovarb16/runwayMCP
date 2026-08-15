import pytest

import server


@pytest.mark.integration
def test_both_tools_registered():
    """Both check_visa_sponsorship and fetch_job_posting must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "check_visa_sponsorship" in registered_names
    assert "fetch_job_posting" in registered_names


@pytest.mark.integration
def test_profile_tools_registered():
    """get_profile stays registered; setup_profile/update_profile are gone (SC-17)."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "get_profile" in registered_names
    assert "setup_profile" not in registered_names
    assert "update_profile" not in registered_names


@pytest.mark.integration
def test_analyze_job_registered():
    """analyze_job must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "analyze_job" in registered_names


@pytest.mark.integration
def test_analyze_match_not_registered():
    """analyze_match was removed in Option A — replaced by get_profile + Claude."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "analyze_match" not in registered_names


def test_playwright_warning_emitted_to_stderr_when_unavailable(capsys, monkeypatch):
    """A warning must be printed to stderr when _PLAYWRIGHT_AVAILABLE is False."""
    import tools.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "_PLAYWRIGHT_AVAILABLE", False)

    # Re-execute the startup warning block that server.py will run
    import server as server_mod

    server_mod._warn_if_playwright_missing()

    captured = capsys.readouterr()
    assert captured.err != "", "Expected a warning on stderr, got nothing"
    assert "playwright" in captured.err.lower()
    assert "pip install playwright" in captured.err


def test_playwright_warning_not_emitted_when_available(capsys, monkeypatch):
    """No warning should be printed when Playwright is available."""
    import tools.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "_PLAYWRIGHT_AVAILABLE", True)

    import server as server_mod

    server_mod._warn_if_playwright_missing()

    captured = capsys.readouterr()
    assert captured.err == ""


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


def test_sc07_refresh_called_at_startup(monkeypatch):
    """SC-07: refresh_to_latest_fy() must be called exactly once when server.py
    is imported (module-level startup block), after all mcp.tool() registrations."""
    import importlib
    import unittest.mock as mock

    # Patch before reload so the module-level call hits our mock
    with mock.patch("tools.uscis_cache.refresh_to_latest_fy") as mock_refresh:
        import server as server_mod

        importlib.reload(server_mod)

    mock_refresh.assert_called_once()

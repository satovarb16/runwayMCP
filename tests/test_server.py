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
    """setup_profile and update_profile must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "setup_profile" in registered_names
    assert "update_profile" in registered_names


@pytest.mark.integration
def test_analyze_match_registered():
    """analyze_match must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "analyze_match" in registered_names


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

import pytest

import server


@pytest.mark.integration
def test_both_tools_registered():
    """Both check_visa_sponsorship and fetch_job_posting must be registered in the FastMCP instance."""
    tool_manager = server.mcp._tool_manager
    registered_names = set(tool_manager._tools.keys())
    assert "check_visa_sponsorship" in registered_names
    assert "fetch_job_posting" in registered_names

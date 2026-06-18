import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

from mcp.server.fastmcp import FastMCP

from tools.analyze import analyze_job
from tools.jobs import fetch_job_posting
from tools.match import analyze_match
from tools.profile import setup_profile, update_profile
from tools.uscis_cache import refresh_to_latest_fy
from tools.visa import check_visa_sponsorship


def _warn_if_playwright_missing() -> None:
    """Print a warning to stderr when Playwright is not installed."""
    import tools.jobs as _jobs_mod

    if not _jobs_mod._PLAYWRIGHT_AVAILABLE:
        print(
            "WARNING: Playwright is not installed — some JavaScript-heavy job boards "
            "may fail to parse.\n"
            "To fix: pip install playwright && playwright install chromium",
            file=sys.stderr,
        )


mcp = FastMCP("runway-mcp")

mcp.tool()(check_visa_sponsorship)
mcp.tool()(fetch_job_posting)
mcp.tool()(setup_profile)
mcp.tool()(update_profile)
mcp.tool()(analyze_match)
mcp.tool()(analyze_job)

_warn_if_playwright_missing()
refresh_to_latest_fy()

if __name__ == "__main__":
    mcp.run(transport="stdio")

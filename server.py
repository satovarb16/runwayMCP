import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

from mcp.server.fastmcp import FastMCP

from tools.analyze import analyze_job
from tools.jobs import fetch_job_posting
from tools.jobs_store import list_jobs, save_job_analysis, set_application_status
from tools.profile import get_profile
from tools.resumes import get_resume_version, list_resume_versions, save_resume_version
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
mcp.tool()(get_profile)
mcp.tool()(analyze_job)
mcp.tool()(save_job_analysis)
mcp.tool()(list_jobs)
mcp.tool()(set_application_status)
mcp.tool()(save_resume_version)
mcp.tool()(get_resume_version)
mcp.tool()(list_resume_versions)

_warn_if_playwright_missing()
refresh_to_latest_fy()


def main() -> None:
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        pass  # clean exit on Ctrl+C when run interactively


if __name__ == "__main__":
    main()

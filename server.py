import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

from mcp.server.fastmcp import FastMCP

from tools.jobs import fetch_job_posting
from tools.visa import check_visa_sponsorship

mcp = FastMCP("runway-mcp")

mcp.tool()(check_visa_sponsorship)
mcp.tool()(fetch_job_posting)

if __name__ == "__main__":
    mcp.run(transport="stdio")

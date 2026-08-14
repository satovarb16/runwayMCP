"""Manual end-to-end verification driver for the persistence-layer tools.

Drives the REAL stdio MCP server (`python -m server`) over JSON-RPC via the
official mcp client — not import-and-call. Isolates the job store by pointing
HOME/USERPROFILE at a throwaway temp dir so the user's real
~/.config/runway-mcp/jobs.json is never touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

J1 = "https://jobs.example.com/senior-eng"
J2 = "https://jobs.example.com/junior-eng"
J3 = "https://jobs.example.com/pm-role"


def _envelope(result) -> dict:
    """Pull the tool's returned JSON envelope out of a CallToolResult."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": text}
    return {"_empty": True}


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    env = _envelope(res)
    print(f"\n>>> {name}({json.dumps(args)})")
    print(json.dumps(env, indent=2, default=str))
    return env


async def main() -> None:
    tmp_home = tempfile.mkdtemp(prefix="runway-verify-")
    env = dict(os.environ)
    env["USERPROFILE"] = tmp_home
    env["HOME"] = tmp_home
    print(f"# Isolated store HOME = {tmp_home}")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server"],
        env=env,
        cwd=os.getcwd(),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"\n# Registered tools ({len(names)}): {names}")
            for t in ("save_job_analysis", "list_jobs", "set_application_status"):
                assert t in names, f"MISSING TOOL: {t}"

            # --- Happy path ---
            await call(
                session,
                "save_job_analysis",
                url=J1,
                title="Senior Engineer",
                company="Acme",
                visa_verdict="GREEN",
                score=85,
                recommendation="APPLY",
            )
            await call(
                session,
                "save_job_analysis",
                url=J2,
                title="Junior Engineer",
                company="Beta",
                visa_verdict="RED",
                score=40,
                recommendation="SKIP",
            )
            await call(
                session,
                "save_job_analysis",
                url=J3,
                title="Product Manager",
                company="Gamma",
                visa_verdict="UNKNOWN",
            )  # no score

            await call(
                session, "list_jobs", sort_by="score"
            )  # expect J1, J2, then J3(None last)

            await call(
                session,
                "set_application_status",
                url=J1,
                status="applied",
                notes="applied via referral",
            )
            await call(session, "list_jobs", status="applied")  # expect only J1

            # --- PROBES (the bugs judgment-day fixed) ---
            print("\n# ===== PROBES =====")
            # P1: float score must NOT crash the boundary -> error envelope
            await call(
                session,
                "save_job_analysis",
                url="https://x.com/p1",
                title="X",
                company="X",
                visa_verdict="GREEN",
                score=85.5,
            )
            # P2: upsert must PRESERVE status="applied" on the already-applied J1
            await call(
                session,
                "save_job_analysis",
                url=J1,
                title="Senior Engineer (reposted)",
                company="Acme",
                visa_verdict="GREEN",
                score=90,
                recommendation="APPLY",
            )
            applied_after = await call(session, "list_jobs", status="applied")
            # P3: since with Z suffix, inclusive
            await call(session, "list_jobs", since="2000-01-01T00:00:00Z")  # expect all
            # P4: limit=0 -> error envelope, not silent empty
            await call(session, "list_jobs", limit=0)
            # P5: set_application_status unknown url -> not_found
            await call(
                session,
                "set_application_status",
                url="https://x.com/nope",
                status="applied",
            )

            # Assertion summary for P2 (the headline feature)
            j1_still_applied = any(
                j.get("url") == J1 and j.get("status") == "applied"
                for j in applied_after.get("jobs", [])
            )
            print(f"\n# P2 CHECK — J1 still applied after re-save: {j1_still_applied}")


if __name__ == "__main__":
    asyncio.run(main())

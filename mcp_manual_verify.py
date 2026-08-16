"""Manual end-to-end verification driver for the persistence-layer tools.

Drives the REAL stdio MCP server (`python -m server`) over JSON-RPC via the
official mcp client — not import-and-call. Isolates the SQLite store by
pointing HOME/USERPROFILE at a throwaway temp dir so the user's real
~/.config/runway-mcp/runway.db is never touched.

FINAL 0.3.0 contract: save_job_analysis takes title/company/country (no
visa_verdict — that field is gone with no replacement column) and requires
at least one of url/custom_title. save_resume_version/list_resume_versions
take job_id, not job_url — job_id is a real foreign key against a job saved
via save_job_analysis, so that must run first.
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
            expected = {
                "analyze_job",
                "save_job_analysis",
                "get_job",
                "list_jobs",
                "set_application_status",
                "save_resume_version",
                "get_resume_version",
                "list_resume_versions",
                "set_work_authorization",
            }
            assert set(names) == expected, f"tool set mismatch: {set(names) ^ expected}"

            # --- Setup: resume + work authorization (both analyze_job preconditions) ---
            base_resume = await call(
                session,
                "save_resume_version",
                content="Jane Doe — Python engineer",
                label="General",
                parent_id=None,
            )
            await call(session, "set_work_authorization", countries=["United States"])

            # --- Happy path: save 3 jobs ---
            j1 = await call(
                session,
                "save_job_analysis",
                url=J1,
                title="Senior Engineer",
                company="Acme",
                country="United States",
                score=85,
                recommendation="APPLY",
            )
            await call(
                session,
                "save_job_analysis",
                url=J2,
                title="Junior Engineer",
                company="Beta",
                country="United States",
                score=40,
                recommendation="SKIP",
            )
            await call(
                session,
                "save_job_analysis",
                url=J3,
                title="Product Manager",
                company="Gamma",
                country="Germany",
            )  # no score

            await call(
                session, "list_jobs", sort_by="score"
            )  # expect J1, J2, then J3(None last)

            # --- The headline query: company filter ---
            await call(session, "list_jobs", company="acme")  # expect only J1

            await call(
                session,
                "set_application_status",
                id=j1["id"],
                status="applied",
                notes="applied via referral",
            )
            await call(session, "list_jobs", status="applied")  # expect only J1

            # --- get_job: linked resume version summaries ---
            await call(
                session,
                "save_resume_version",
                content="Jane Doe — tailored for Acme",
                label="Acme tailored",
                parent_id=base_resume["id"],
                job_id=j1["id"],
            )
            await call(session, "get_job", id=j1["id"])

            # --- URL-less job via custom_title ---
            j_referral = await call(
                session,
                "save_job_analysis",
                title="Staff Engineer",
                company="Delta",
                country="Canada",
                custom_title="Delta referral role",
            )
            await call(session, "get_job", custom_title="Delta referral role")

            # --- PROBES (the bugs judgment-day fixed, re-checked on the new contract) ---
            print("\n# ===== PROBES =====")
            # P1: at least one of url/custom_title required -> error envelope, not a crash
            await call(
                session,
                "save_job_analysis",
                title="X",
                company="X",
                country="United States",
            )
            # P2: upsert must PRESERVE status="applied" on the already-applied J1
            await call(
                session,
                "save_job_analysis",
                url=J1,
                title="Senior Engineer (reposted)",
                company="Acme",
                country="United States",
                score=90,
                recommendation="APPLY",
            )
            applied_after = await call(session, "list_jobs", status="applied")
            # P3: since with Z suffix, inclusive
            await call(session, "list_jobs", since="2000-01-01T00:00:00Z")  # expect all
            # P4: limit=0 -> error envelope, not silent empty
            await call(session, "list_jobs", limit=0)
            # P5: set_application_status unknown id -> not_found
            await call(session, "set_application_status", id="nope", status="applied")
            # P6: save_resume_version with an unknown job_id -> job_not_found, not a raw error
            await call(
                session,
                "save_resume_version",
                content="tailored",
                label="Tailored for a ghost job",
                parent_id=base_resume["id"],
                job_id="does-not-exist",
            )
            # P7: analyze_job never takes jd_text as a parameter. Note: the MCP
            # wire protocol validates a tool call's arguments against the
            # tool's advertised JSON schema BEFORE the Python function is
            # ever invoked, so an unrecognized field is rejected (or dropped,
            # depending on client/schema strictness) at that layer — the
            # "raises TypeError for an unexpected kwarg" guarantee is proven
            # at the Python function level (tests/test_docs_audit.py,
            # tests/test_analyze.py), not necessarily reproducible verbatim
            # over this wire call. Both layers refusing the field, by
            # whichever mechanism, is what SC-34 requires.
            p7_result = await call(
                session,
                "analyze_job",
                title="X",
                company="X",
                country="United States",
                jd_text="should be rejected",
            )
            print(f"\n# P7 CHECK — call result: {p7_result}")

            # Assertion summary for P2 (the headline feature)
            j1_still_applied = any(
                j.get("id") == j1["id"] and j.get("status") == "applied"
                for j in applied_after.get("jobs", [])
            )
            print(f"\n# P2 CHECK — J1 still applied after re-save: {j1_still_applied}")
            print(f"\n# Referral job saved without a URL: {j_referral.get('id')}")


if __name__ == "__main__":
    asyncio.run(main())

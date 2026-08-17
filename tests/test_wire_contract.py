"""What the MCP transport actually guarantees, checked over a real stdio server.

Every other test in this suite calls the tool functions directly from Python.
That is the wrong layer for questions about argument handling: between a caller
and the function sits FastMCP's JSON-RPC dispatch, which validates the call
against the tool's advertised JSON schema. Two behaviours follow, and they are
not symmetric:

  * a MISSING REQUIRED argument is rejected before the function runs — this is
    a real guarantee, and it is what these tests pin;
  * an UNRECOGNISED argument is dropped before the function runs, so the
    function can never object to it.

The second is why spec scenario SC-34 was withdrawn. It claimed analyze_job
"rejects" an unexpected `jd_text`; over the wire the argument simply evaporates.
A guarantee that cannot hold where callers live is worse than none, because it
reads as a promise. The cost is real but deferred: a typo in an optional
argument silently becomes "not provided", and the user finds out much later —
`custom_titel` instead of `custom_title` saves a job with no handle, and the
lookup that should find it months on returns not_found instead.

These spawn `python -m server` as a subprocess, so they are slower than the rest
of the suite and marked `integration`. HOME/USERPROFILE point at a throwaway
directory so a developer's real store is never touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import pytest

pytestmark = pytest.mark.integration


def _envelope(result) -> dict:
    """Pull a tool's returned JSON envelope out of a CallToolResult."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": text}
    return {}


async def _drive(calls):
    """Run `calls(session)` against a live stdio server in an isolated HOME."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    home = tempfile.mkdtemp(prefix="runway-wire-")
    env = dict(os.environ)
    env["HOME"] = home
    env["USERPROFILE"] = home

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server"],
        env=env,
        cwd=os.getcwd(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await calls(session)


def _run(calls):
    # pytest-asyncio was removed with analyze_job's async def, so the loop is
    # managed here rather than by a plugin.
    return asyncio.run(_drive(calls))


def test_missing_required_argument_is_rejected_before_the_tool_runs():
    """The guarantee the transport DOES enforce.

    analyze_job requires country. Omitting it must fail at dispatch, not
    reach the function and not return a success envelope.
    """

    async def calls(session):
        return await session.call_tool(
            "analyze_job", {"title": "SWE", "company": "Acme"}
        )

    result = _run(calls)

    assert result.isError is True
    assert "country" in str(result.content[0].text)


def test_required_argument_check_covers_every_registered_tool():
    """Derived from the server's own registrations, not a hardcoded list.

    A hardcoded list would just move the staleness into the test — the same
    reasoning behind test_manifest_tools_match_registered_tools.
    """

    async def calls(session):
        listed = await session.list_tools()
        out = {}
        for tool in listed.tools:
            required = (tool.inputSchema or {}).get("required") or []
            if not required:
                continue  # nothing to omit
            out[tool.name] = await session.call_tool(tool.name, {})
        return out

    results = _run(calls)

    assert results, "expected at least one tool with a required argument"
    for name, result in results.items():
        assert result.isError is True, f"{name} accepted a call with no arguments"


def test_unrecognised_argument_is_dropped_not_rejected():
    """The behaviour SC-34 assumed away, pinned so it cannot surprise us twice.

    This is documentation-as-test: it asserts the transport's actual, somewhat
    unhelpful behaviour. If a future MCP version starts rejecting unknown
    fields, this test fails and SC-34 can be reinstated deliberately rather
    than rediscovered by accident.
    """

    async def calls(session):
        await session.call_tool(
            "save_resume_version",
            {"content": "cv", "label": "Base", "parent_id": None},
        )
        await session.call_tool(
            "set_work_authorization", {"countries": ["United States"]}
        )
        return await session.call_tool(
            "analyze_job",
            {
                "title": "SWE",
                "company": "Acme",
                "country": "United States",
                "jd_text": "the whole posting, which should never travel here",
            },
        )

    result = _run(calls)

    assert result.isError is False
    assert _envelope(result).get("error") is None

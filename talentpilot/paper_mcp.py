"""InstSci stdio MCP, metadata-only allowlist. No PDF downloads or institution changes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

ALLOWED = {"search_papers", "get_paper_metadata"}


async def call(tool, arguments):
    if tool not in ALLOWED | {"tools/list"}:
        raise ValueError("MCP tool is outside the read-only metadata allowlist")
    if tool == "search_papers":
        if set(arguments) - {"query", "limit", "year_range"}:
            raise ValueError("Unknown paper search argument")
        if not isinstance(arguments.get("query"), str) or not 1 <= len(arguments["query"]) <= 300:
            raise ValueError("Explicit public research query required")
        if not isinstance(arguments.get("limit", 5), int) or not 1 <= arguments.get("limit", 5) <= 20:
            raise ValueError("Search limit must be 1..20")
    elif tool == "get_paper_metadata":
        if set(arguments) != {"doi"} or not isinstance(arguments["doi"], str) or not arguments["doi"].startswith("10.") or len(arguments["doi"]) > 250:
            raise ValueError("Explicit DOI required")
    elif arguments:
        raise ValueError("tools/list accepts no arguments")
    import sys
    local_command = Path(sys.executable).parent / "instsci-mcp"
    command = os.getenv("INSTSCI_MCP_COMMAND", "") or (str(local_command) if local_command.is_file() else "")
    if not command or not Path(command).is_absolute() or not Path(command).is_file():
        raise ValueError("Set INSTSCI_MCP_COMMAND to the approved absolute instsci-mcp executable")
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        raise ValueError("Install requirements-mcp.txt before connecting InstSci") from None
    parameters = StdioServerParameters(command=command, args=[],
                                     env={k: v for k, v in os.environ.items()
                                          if k in {"HOME", "PATH", "LANG", "SYSTEMROOT", "TEMP", "TMP"}})
    async def invoke():
        with open(os.devnull, "w") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    discovered = {t.name for t in listing.tools}
                    available = sorted(discovered & ALLOWED)
                    if tool == "tools/list":
                        return {"status": "handshake_verified", "allowed_tools": available,
                                "blocked_tools": sorted(discovered - ALLOWED)}
                    if tool not in discovered:
                        raise ValueError("Approved server does not expose the requested tool")
                    result = await session.call_tool(tool, arguments)
                    if result.isError:
                        raise ValueError("InstSci tool returned an error; no successful result committed")
                    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                    if len(text) > 150000:
                        raise ValueError("MCP response exceeded metadata size budget")
                    # Upstream conflates provider failures and empty results; never certify an empty search.
                    ambiguous = text.strip() == "No results found." or text.startswith("Paper not found")
                    return {"status": "empty_or_provider_unavailable" if ambiguous else "tool_completed",
                            "tool": tool, "text": text,
                            "evidence": "provider_metadata_not_resume_or_author_identity_verification",
                            "downloads": 0}
    try:
        return await asyncio.wait_for(invoke(), timeout=45)
    except asyncio.TimeoutError:
        raise ValueError("InstSci MCP timed out; inspect provider availability") from None
    except Exception as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError("InstSci MCP connection failed; check the approved server installation") from None

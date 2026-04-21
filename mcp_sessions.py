"""Open MCP sessions once at startup and return the listed tools + a name registry.

We follow the canonical Strands pattern from
https://strandsagents.com/docs/user-guide/concepts/tools/mcp-tools/:

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        agent = Agent(tools=tools)

The ExitStack owns every MCPClient for the lifetime of the process (so the
session stays alive across A2A requests), and we hand the LISTED tools to
every Agent — never the MCPClient object itself. That way no Agent ever
re-enters an already-open session, and the tools actually appear in the
agent's registry.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Any

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

from .settings import Settings

log = logging.getLogger(__name__)


def _tool_name(tool: Any) -> str:
    for attr in ("tool_name", "name"):
        value = getattr(tool, attr, None)
        if isinstance(value, str):
            return value
    spec = getattr(tool, "tool_spec", None)
    if isinstance(spec, dict) and isinstance(spec.get("name"), str):
        return spec["name"]
    raise AttributeError(f"Cannot determine tool name for {tool!r}")


def open_sessions(
    settings: Settings,
    stack: ExitStack,
) -> tuple[dict[str, list[Any]], dict[str, list[str]]]:
    """Open every configured MCP session and return (tools_by_source, names_by_source).

    - `tools_by_source`: {"servicenow": [<AgentTool>, ...], "box": [...], ...}
      These Tool objects can be shared freely across multiple Agents.
    - `names_by_source`: {"servicenow": ["servicenow_get_record", ...], ...}
      Used by `build_tto_task_list` to resolve `tool_sources: ["servicenow"]`
      into the concrete (prefixed) tool names the `workflow` tool needs for
      per-task scoping.
    """
    tools_by_source: dict[str, list[Any]] = {}
    names_by_source: dict[str, list[str]] = {}

    for source, url in settings.sources.items():
        try:
            client = MCPClient(
                lambda u=url: streamablehttp_client(u),
                prefix=source,
            )
            stack.enter_context(client)
            tools = list(client.list_tools_sync())
        except Exception as exc:  # noqa: BLE001 — a missing MCP shouldn't crash startup
            log.warning("MCP source %r at %s unavailable: %s", source, url, exc)
            tools_by_source[source] = []
            names_by_source[source] = []
            continue

        tools_by_source[source] = tools
        names_by_source[source] = [_tool_name(t) for t in tools]
        log.info(
            "MCP %s: %d tools (%s)",
            source,
            len(tools),
            ", ".join(names_by_source[source]) or "none",
        )

    return tools_by_source, names_by_source

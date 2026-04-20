"""Open MCP sessions once at startup and return the clients + a name registry."""

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
) -> tuple[dict[str, MCPClient], dict[str, list[str]]]:
    """Open every configured MCP session and return (clients_by_source, names_by_source).

    Strands `Agent(tools=[mcp_client])` accepts MCPClient objects directly, so we
    return the clients themselves keyed by source name. We still call
    `list_tools_sync()` to build the source->tool-names registry that
    `build_tto_task_list` uses to resolve `tool_sources: ["servicenow"]` into
    the concrete tool names the `workflow` tool needs for per-task scoping.
    """
    clients: dict[str, MCPClient] = {}
    registry: dict[str, list[str]] = {}

    for source, url in settings.sources.items():
        try:
            client = MCPClient(
                lambda u=url: streamablehttp_client(u),
                prefix=f"{source}_",
            )
            stack.enter_context(client)
            names = [_tool_name(t) for t in client.list_tools_sync()]
        except Exception as exc:  # noqa: BLE001 — a missing MCP shouldn't crash startup
            log.warning("MCP source %r at %s unavailable: %s", source, url, exc)
            registry[source] = []
            continue

        clients[source] = client
        registry[source] = names
        log.info("MCP %s: %d tools (%s)", source, len(names), ", ".join(names) or "none")

    return clients, registry

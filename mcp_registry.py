"""MCP client setup with env-driven endpoint settings."""

from __future__ import annotations

from mcp.client.streamable_http import streamablehttp_client
from pydantic_settings import BaseSettings, SettingsConfigDict
from strands.tools.mcp import MCPClient

TOOL_PREFIXES: dict[str, str] = {
    "servicenow": "snow",
    "confluence": "confluence",
    "github": "github",
}


class MCPServerSettings(BaseSettings):
    """
    MCP endpoint settings loaded from environment or optional JSON config.

    Environment variables:
      - TTO_SERVICENOW_MCP_URL
      - TTO_CONFLUENCE_MCP_URL
      - TTO_GITHUB_MCP_URL
    """

    model_config = SettingsConfigDict(
        env_prefix="TTO_",
        env_file=".env",
        extra="ignore",
    )

    # BaseSettings automatically resolves env vars first; these are only defaults.
    servicenow_mcp_url: str = "http://localhost:8001/mcp"
    confluence_mcp_url: str = "http://localhost:8002/mcp"
    github_mcp_url: str = "http://localhost:8003/mcp"

    def as_server_map(self) -> dict[str, str]:
        """Return normalized server name -> URL mapping."""
        return {
            "servicenow": self.servicenow_mcp_url,
            "confluence": self.confluence_mcp_url,
            "github": self.github_mcp_url,
        }


def create_mcp_clients(settings: MCPServerSettings | None = None) -> dict[str, MCPClient]:
    """Create MCP clients for all configured backends using BaseSettings resolution."""
    resolved_settings = settings or MCPServerSettings()
    servers = resolved_settings.as_server_map()
    clients: dict[str, MCPClient] = {}
    for key, url in servers.items():
        clients[key] = MCPClient(
            lambda url=url: streamablehttp_client(url),
            prefix=TOOL_PREFIXES.get(key, key),
        )
    return clients

"""Settings for the TTO checklist A2A agent (MCP URLs, server host/port, model)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTO_", env_file=".env", extra="ignore")

    servicenow_url: str = Field("http://127.0.0.1:9101/mcp", alias="MCP_SERVICENOW_URL")
    github_url: str = Field("http://127.0.0.1:9102/mcp", alias="MCP_GITHUB_URL")
    confluence_url: str = Field("http://127.0.0.1:9103/mcp", alias="MCP_CONFLUENCE_URL")
    aws_url: str = Field("http://127.0.0.1:9104/mcp", alias="MCP_AWS_URL")
    azure_url: str = Field("http://127.0.0.1:9105/mcp", alias="MCP_AZURE_URL")
    box_url: str = Field("http://127.0.0.1:9106/mcp", alias="MCP_BOX_URL")

    host: str = Field("127.0.0.1", alias="TTO_HOST")
    port: int = Field(9000, alias="TTO_PORT")
    model: str = Field(
        "us.anthropic.claude-sonnet-4-20250514-v1:0",
        alias="TTO_MODEL",
        description="Bedrock model id (or any Strands-compatible model id).",
    )

    @property
    def sources(self) -> dict[str, str]:
        """Map of source name -> MCP URL."""
        return {
            "servicenow": self.servicenow_url,
            "github": self.github_url,
            "confluence": self.confluence_url,
            "aws": self.aws_url,
            "azure": self.azure_url,
            "box": self.box_url,
        }

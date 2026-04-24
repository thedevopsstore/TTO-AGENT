"""Settings for the TTO checklist AG-UI agent (MCP URLs, server host/port, model)."""

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

    # AG-UI server defaults match Bedrock AgentCore Runtime's AG-UI contract:
    # bind to 0.0.0.0, port 8080, POST /invocations.
    host: str = Field("0.0.0.0", alias="TTO_HOST")
    port: int = Field(8080, alias="TTO_PORT")
    agent_path: str = Field(
        "/invocations",
        alias="TTO_AGENT_PATH",
        description="HTTP path the AG-UI SSE endpoint is mounted on.",
    )
    agent_id: str = Field(
        "tto-validator",
        alias="TTO_AGENT_ID",
        description=(
            "Agent id exposed to AG-UI clients (CopilotKit references this in "
            "CopilotRuntime.agents)."
        ),
    )

    # ############################################################
    # LiteLLM Configuration
    # ############################################################

    lite_llm_api_key: str = Field(default="",
        alias="LITELLM_API_KEY",
        description="LiteLLM API key.",
    )
    litellm_host: str = Field(default="http://localhost:8000",
        alias="LITELLM_HOST",
        description="LiteLLM host.",
    )

    model: str = Field(
        "bedrock-claude-4.5-sonnet",
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

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    aws_mcp_endpoint: str
    aws_mcp_region: str = "us-east-1"
    aws_mcp_service: str | None = None  # None = inferred from URL
    aws_role_name: str = "McpExecutionRole"
    aws_bootstrap_profile: str | None = None  # for local dev; uses instance role if unset
    local_deployment: bool = False  # skip STS; use default credential chain for all calls
    sts_refresh_window_seconds: int = 300
    wrapper_host: str = "0.0.0.0"
    wrapper_port: int = 8000
    wrapper_log_level: str = "INFO"

from __future__ import annotations

import logging

from fastmcp import FastMCP

from .config import Settings
from .credentials import StsCredentialManager
from .middleware import AccountRoutingMiddleware


def _proxy_config(s: Settings) -> dict:
    args = [
        "mcp-proxy-for-aws@latest",
        s.aws_mcp_endpoint,
        "--metadata",
        f"AWS_REGION={s.aws_mcp_region}",
    ]
    if s.aws_mcp_service:
        args += ["--service", s.aws_mcp_service]
    if s.aws_bootstrap_profile:
        args += ["--profile", s.aws_bootstrap_profile]
    return {
        "mcpServers": {
            "aws-mcp": {
                "command": "uvx",
                "args": args,
                "transportType": "stdio",
            }
        }
    }


def main() -> None:
    s = Settings()
    logging.basicConfig(
        level=s.wrapper_log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    creds = StsCredentialManager(
        role_name=s.aws_role_name,
        refresh_window_seconds=s.sts_refresh_window_seconds,
    )
    server = FastMCP.as_proxy(_proxy_config(s), name="aws-mcp-wrapper")
    server.add_middleware(AccountRoutingMiddleware(s, creds))
    server.run(transport="streamable-http", host=s.wrapper_host, port=s.wrapper_port)


if __name__ == "__main__":
    main()

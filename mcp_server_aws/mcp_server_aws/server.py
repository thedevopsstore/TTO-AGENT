from __future__ import annotations

import logging

from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy

from .config import Settings
from .credentials import StsCredentialManager
from .middleware import AccountRoutingMiddleware


def main() -> None:
    s = Settings()
    logging.basicConfig(
        level=s.wrapper_log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    args = [
        "mcp-proxy-for-aws@latest",
        s.aws_mcp_endpoint,
        "--region", s.aws_mcp_region,
        "--log-level", s.wrapper_log_level,
    ]
    if s.aws_bootstrap_profile:
        args += ["--profile", s.aws_bootstrap_profile]

    server = create_proxy(
        StdioTransport(command="uvx", args=args, keep_alive=True),
        name="aws-mcp-wrapper",
    )

    creds = StsCredentialManager(role_name=s.aws_role_name)
    server.add_middleware(AccountRoutingMiddleware(s, creds))
    server.run(transport="streamable-http", host=s.wrapper_host, port=s.wrapper_port)


if __name__ == "__main__":
    main()

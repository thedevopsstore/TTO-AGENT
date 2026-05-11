from __future__ import annotations

import logging

from fastmcp import FastMCP

from .config import Settings
from .credentials import StsCredentialManager
from .middleware import AccountRoutingMiddleware


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
    server = FastMCP("aws-mcp-wrapper")
    server.add_middleware(AccountRoutingMiddleware(s, creds))
    server.run(transport="streamable-http", host=s.wrapper_host, port=s.wrapper_port, stateless_http=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging
from typing import Any

from botocore.credentials import Credentials
from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

from .config import Settings
from .credentials import CachedCredentials

log = logging.getLogger(__name__)


async def call_upstream_tool(
    settings: Settings,
    creds: CachedCredentials,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Call one upstream AWS MCP tool using explicit temporary credentials."""
    boto_creds = Credentials(
        access_key=creds.access_key_id,
        secret_key=creds.secret_access_key,
        token=creds.session_token,
    )
    client = aws_iam_streamablehttp_client(
        endpoint=settings.aws_mcp_endpoint,
        aws_region=settings.aws_mcp_region,
        aws_service=settings.aws_mcp_service,
        credentials=boto_creds,
    )
    async with client as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)

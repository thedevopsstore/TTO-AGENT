from __future__ import annotations

import logging
from typing import Any

import boto3
import httpx
from botocore.credentials import Credentials, RefreshableCredentials
from fastmcp.tools.base import ToolResult
from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

from .config import Settings

log = logging.getLogger(__name__)


def _http_client_factory(settings: Settings):
    """Return an httpx_client_factory with the configured SSL verification."""
    verify: bool | str = True
    if settings.ssl_verify.lower() == "false":
        verify = False
    elif settings.ssl_verify.lower() != "true":
        verify = settings.ssl_verify  # treat as path to CA bundle

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers or {},
            timeout=timeout or httpx.Timeout(30.0),
            auth=auth,
            verify=verify,
        )

    return factory


def _bootstrap_creds(settings: Settings) -> Credentials:
    """Resolve credentials via the standard boto3 chain (env vars, ~/.aws, instance role)."""
    session = boto3.Session(profile_name=settings.aws_bootstrap_profile)
    frozen = session.get_credentials().get_frozen_credentials()
    return Credentials(
        access_key=frozen.access_key,
        secret_key=frozen.secret_key,
        token=frozen.token,
    )


async def call_upstream_tool(
    settings: Settings,
    creds: RefreshableCredentials | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Call one upstream AWS MCP tool.

    Pass creds=None to use the default credential chain (local_deployment mode).
    RefreshableCredentials are passed directly - botocore auto-refreshes before expiry.
    """
    boto_creds = _bootstrap_creds(settings) if creds is None else creds
    client = aws_iam_streamablehttp_client(
        endpoint=settings.aws_mcp_endpoint,
        aws_region=settings.aws_mcp_region,
        aws_service=settings.aws_mcp_service,
        credentials=boto_creds,
        httpx_client_factory=_http_client_factory(settings),
    )
    async with client as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return ToolResult(
                content=result.content,
                structured_content=result.structuredContent,
            )

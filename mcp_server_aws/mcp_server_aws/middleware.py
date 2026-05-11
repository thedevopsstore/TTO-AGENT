from __future__ import annotations

import copy
import logging
import re
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import CallToolResult, TextContent, Tool

from .config import Settings
from .credentials import StsCredentialManager
from .upstream import call_upstream_tool

log = logging.getLogger(__name__)

ACCOUNT_ID = "account_id"
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")

_ACCOUNT_ID_SCHEMA = {
    "type": "string",
    "description": "12-digit AWS account ID to target.",
    "pattern": r"^\d{12}$",
}


def _inject_account_id(tool: Tool) -> Tool:
    """Return a copy of tool with account_id prepended to its input schema. Idempotent."""
    schema = copy.deepcopy(tool.inputSchema or {"type": "object", "properties": {}})
    props: dict = schema.setdefault("properties", {})
    required: list = schema.setdefault("required", [])

    if ACCOUNT_ID in props:
        return tool

    schema["properties"] = {ACCOUNT_ID: _ACCOUNT_ID_SCHEMA, **props}
    if ACCOUNT_ID not in required:
        required.insert(0, ACCOUNT_ID)

    patched = copy.copy(tool)
    patched.inputSchema = schema
    return patched


def _error_result(msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"ERROR: {msg}")],
        isError=True,
    )


def _maybe_invalidate(exc: Exception, account_id: str, creds: StsCredentialManager) -> None:
    """Invalidate cached STS creds when the upstream returns an auth error."""
    if any(kw in str(exc).lower() for kw in ("403", "forbidden", "expired", "token")):
        log.info("invalidating credentials for account %s after auth error", account_id)
        creds.invalidate(account_id)


class AccountRoutingMiddleware(Middleware):
    """Inject account_id into tool schemas; route calls with per-account STS creds."""

    def __init__(self, settings: Settings, credentials: StsCredentialManager) -> None:
        self._settings = settings
        self._credentials = credentials

    async def on_list_tools(self, context: MiddlewareContext, call_next) -> Any:
        result = await call_next(context)
        result.tools = [_inject_account_id(t) for t in result.tools]
        return result

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        args = dict(context.message.arguments or {})
        account_id = args.pop(ACCOUNT_ID, None)

        if not account_id:
            return _error_result(f"Missing required argument: {ACCOUNT_ID}")

        if not _ACCOUNT_ID_RE.match(str(account_id)):
            return _error_result(f"Invalid account_id format: must be 12 digits, got {account_id!r}")

        try:
            creds = await self._credentials.get(str(account_id))
        except (ValueError, RuntimeError) as exc:
            log.error("credential fetch failed: %s", exc)
            return _error_result(f"Credential error: {exc}")

        log.info("routing tool=%s account_id=%s", context.message.name, account_id)
        try:
            return await call_upstream_tool(self._settings, creds, context.message.name, args)
        except Exception as exc:
            log.exception("upstream call failed for tool %r on account %s", context.message.name, account_id)
            _maybe_invalidate(exc, account_id, self._credentials)
            return _error_result(f"Upstream error: {exc}")

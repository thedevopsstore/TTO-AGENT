from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

ROLE_SESSION_NAME = "mcp-wrapper"


@dataclass
class CachedCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime  # always UTC-aware

    def is_fresh(self, buffer_seconds: int = 300) -> bool:
        remaining = (self.expiration - datetime.now(tz=timezone.utc)).total_seconds()
        return remaining > buffer_seconds


class StsCredentialManager:
    """In-memory STS credential cache, keyed by account_id. Refreshes near expiry."""

    def __init__(self, role_name: str, refresh_window_seconds: int = 300) -> None:
        self._role_name = role_name
        self._refresh_window = refresh_window_seconds
        self._cache: dict[str, CachedCredentials] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    def _role_arn(self, account_id: str) -> str:
        return f"arn:aws:iam::{account_id}:role/{self._role_name}"

    async def get(self, account_id: str) -> CachedCredentials:
        async with self._lock_for(account_id):
            cached = self._cache.get(account_id)
            if cached and cached.is_fresh(self._refresh_window):
                return cached

            fresh = await asyncio.to_thread(self._assume_role, account_id)
            self._cache[account_id] = fresh
            log.info("assumed role for account %s, expires %s", account_id, fresh.expiration)
            return fresh

    def invalidate(self, account_id: str) -> None:
        """Force re-assumption on next call, e.g. after a 403."""
        self._cache.pop(account_id, None)

    def _assume_role(self, account_id: str) -> CachedCredentials:
        role_arn = self._role_arn(account_id)
        try:
            resp = boto3.client("sts").assume_role(
                RoleArn=role_arn,
                RoleSessionName=f"{ROLE_SESSION_NAME}-{account_id}",
            )["Credentials"]
        except ClientError as exc:
            raise RuntimeError(f"STS AssumeRole failed for {role_arn}: {exc}") from exc

        return CachedCredentials(
            access_key_id=resp["AccessKeyId"],
            secret_access_key=resp["SecretAccessKey"],
            session_token=resp["SessionToken"],
            expiration=resp["Expiration"],
        )

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.credentials import RefreshableCredentials
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

ROLE_SESSION_NAME = "mcp-wrapper"


class StsCredentialManager:
    """Per-account RefreshableCredentials cache. botocore handles TTL-based refresh automatically."""

    def __init__(self, role_name: str) -> None:
        self._role_name = role_name
        self._cache: dict[str, RefreshableCredentials] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    def _make_fetcher(self, account_id: str):
        role_arn = f"arn:aws:iam::{account_id}:role/{self._role_name}"

        def fetch() -> dict:
            try:
                resp = boto3.client("sts").assume_role(
                    RoleArn=role_arn,
                    RoleSessionName=f"{ROLE_SESSION_NAME}-{account_id}",
                )["Credentials"]
            except ClientError as exc:
                raise RuntimeError(f"STS AssumeRole failed for {role_arn}: {exc}") from exc

            log.info("assumed role for account %s, expires %s", account_id, resp["Expiration"])
            return {
                "access_key": resp["AccessKeyId"],
                "secret_key": resp["SecretAccessKey"],
                "token": resp["SessionToken"],
                "expiry_time": resp["Expiration"].isoformat(),
            }

        return fetch

    async def get(self, account_id: str) -> RefreshableCredentials:
        async with self._lock_for(account_id):
            if account_id not in self._cache:
                fetch = self._make_fetcher(account_id)

                def create() -> RefreshableCredentials:
                    return RefreshableCredentials.create_from_metadata(
                        metadata=fetch(),
                        refresh_using=fetch,
                        method="assume-role",
                    )

                self._cache[account_id] = await asyncio.to_thread(create)
            return self._cache[account_id]

    def invalidate(self, account_id: str) -> None:
        """Drop cached credentials - forces fresh AssumeRole on next call (e.g. after a 403)."""
        self._cache.pop(account_id, None)

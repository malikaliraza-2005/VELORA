"""Tenant-scoped database access (P2-T5).

The runtime never gets a bare connection. Every query runs inside `Database.tenant()`,
which refuses to start without a `TenantId` and sets `app.tenant_id` for the transaction
only, so RLS (FORCE, policy `tenant_isolation`) scopes everything that follows.

A `TenantId` must come from a trusted source: the bootstrap resolver
(`Database.resolve_channel_tenant`) or an authenticated staff session. Never build one from
a request body, a webhook payload field or AI output (AD-17, AD-20).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import AsyncConnection

PING_TIMEOUT_SECONDS = 5


class TenantContextError(RuntimeError):
    """Raised when database work is attempted without a tenant context."""


@dataclass(frozen=True, slots=True)
class TenantId:
    value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, uuid.UUID):
            raise TypeError("TenantId requires a uuid.UUID")


class ConnectionPool(Protocol):
    def connection(self) -> AbstractAsyncContextManager[AsyncConnection[Any]]: ...


class Database:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def tenant(self, tenant: TenantId) -> AsyncIterator[AsyncConnection[Any]]:
        """One transaction scoped to `tenant`. Commits on success, rolls back on error."""
        if not isinstance(tenant, TenantId):
            raise TenantContextError("database access requires a TenantId")
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_catalog.set_config('app.tenant_id', %s, true)", (str(tenant.value),)
            )
            yield conn

    async def ping(self) -> bool:
        """True if the database answers. Reads nothing (readiness check, P10)."""
        try:
            async with asyncio.timeout(PING_TIMEOUT_SECONDS), self._pool.connection() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            return False
        return True

    async def resolve_channel_tenant(self, provider: str, session_ref: str) -> TenantId | None:
        """Bootstrap: map a provider session reference to its tenant, or None if unknown."""
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT velora.resolve_channel_tenant(%s, %s)", (provider, session_ref)
            )
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return TenantId(row[0])

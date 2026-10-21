"""P2-T5: the database-access helper refuses to run without a tenant context."""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from tests.db.conftest import APP_ROLE, PgDatabase, SeededTenants
from velora.db import Database, TenantContextError, TenantId


@pytest.fixture()
def anyio_backend() -> tuple[str, dict[str, Any]]:
    # psycopg's async mode cannot use the Windows proactor event loop.
    if sys.platform == "win32":
        return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})
    return ("asyncio", {})


class RecordingPool:
    """Stands in for a pool and records whether a connection was ever requested."""

    def __init__(self) -> None:
        self.requested = 0

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[Any]]:
        self.requested += 1
        raise AssertionError("no connection may be taken without a tenant context")
        yield  # pragma: no cover


@pytest.mark.anyio
@pytest.mark.parametrize("tenant", [None, "8d6f1c1e-0000-4000-8000-000000000000", uuid.uuid4()])
async def test_no_tenant_context_raises_before_any_sql(tenant: object) -> None:
    pool = RecordingPool()
    db = Database(pool)
    with pytest.raises(TenantContextError):
        async with db.tenant(cast(TenantId, tenant)):
            pass
    assert pool.requested == 0


def test_tenant_id_requires_a_uuid() -> None:
    with pytest.raises(TypeError):
        TenantId(cast(uuid.UUID, "8d6f1c1e-0000-4000-8000-000000000000"))


@pytest.fixture()
def app_pool_url(db: PgDatabase) -> Iterator[str]:
    yield db.cluster.url(db.name, APP_ROLE)


@pytest.mark.anyio
async def test_tenant_scope_and_resolver_against_postgres(
    app_pool_url: str, tenants: SeededTenants
) -> None:
    async with AsyncConnectionPool(app_pool_url, min_size=1, max_size=2, open=False) as pool:
        db = Database(pool)
        tenant = await db.resolve_channel_tenant("fake", "session-tenant-a")
        assert tenant == TenantId(tenants.a["tenants"])
        assert await db.resolve_channel_tenant("fake", "no-such-session") is None

        async with db.tenant(tenant) as conn:
            cur = await conn.execute("SELECT id FROM velora.orders")
            assert await cur.fetchall() == [(tenants.a["orders"],)]

        # The context ended with the transaction; the pooled connection sees nothing now.
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) FROM velora.orders")
            assert await cur.fetchone() == (0,)


@pytest.mark.anyio
async def test_tenant_scope_rolls_back_on_error(app_pool_url: str, tenants: SeededTenants) -> None:
    tenant = TenantId(tenants.a["tenants"])
    async with AsyncConnectionPool(app_pool_url, min_size=1, max_size=1, open=False) as pool:
        db = Database(pool)
        with pytest.raises(RuntimeError, match="boom"):
            async with db.tenant(tenant) as conn:
                await conn.execute(
                    "UPDATE velora.orders SET delivery_note = 'changed' WHERE id = %s",
                    (tenants.a["orders"],),
                )
                raise RuntimeError("boom")
        async with db.tenant(tenant) as conn:
            cur = await conn.execute(
                "SELECT delivery_note FROM velora.orders WHERE id = %s", (tenants.a["orders"],)
            )
            assert await cur.fetchone() == (None,)

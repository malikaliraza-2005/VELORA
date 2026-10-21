"""Fixtures for the P5 messaging tests: the real database (app_rw and velora_dispatcher), a
simulated clock and the `FakeProvider`. Each test gets its own tenant with one fake channel.

Tests are plain functions that hand an async scenario to `run`, which opens fresh pools on a
new event loop (psycopg's async mode needs the selector loop on Windows).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from tests.db import conftest as db_fixtures
from tests.db.conftest import APP_ROLE, DISPATCHER_ROLE, PgDatabase
from tests.db.seed import seed_tenant
from velora.clock import FakeClock
from velora.db import Database, TenantId
from velora.messaging.dispatch import Dispatcher
from velora.messaging.providers.fake import FakeProvider

cluster = db_fixtures.cluster
db = db_fixtures.db
tenants = db_fixtures.tenants

T0 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Shop:
    tenant: TenantId
    channel_id: uuid.UUID
    session_ref: str


@dataclass
class Env:
    db: Database
    dispatcher: Dispatcher
    clock: FakeClock
    fake: FakeProvider

    @property
    def providers(self) -> dict[str, FakeProvider]:
        return {"fake": self.fake}


def new_shop(pg: PgDatabase, state: str = "connected") -> Shop:
    """A tenant with one fake channel in `state`, created as the administrator."""
    slug = f"m-{uuid.uuid4().hex[:12]}"
    session_ref = f"session-{slug}"
    with pg.admin() as conn, conn.transaction():
        row = conn.execute(
            "INSERT INTO velora.tenants (slug, display_name, order_number_prefix) "
            "VALUES (%s, %s, 'MS') RETURNING id",
            (slug, f"Shop {slug}"),
        ).fetchone()
        assert row is not None
        tenant = row[0]
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        row = conn.execute(
            "INSERT INTO velora.channels (tenant_id, provider, session_ref, state) "
            "VALUES (%s, 'fake', %s, %s) RETURNING id",
            (tenant, session_ref, state),
        ).fetchone()
        assert row is not None
    return Shop(TenantId(tenant), row[0], session_ref)


def new_live_shop(pg: PgDatabase) -> Shop:
    """A tenant ready to take orders (published menu, connected fake channel), with empty
    queues. Unlike the session's shared tenants, tests may change it freely."""
    slug = f"l-{uuid.uuid4().hex[:12]}"
    with pg.admin() as conn:
        ids = seed_tenant(conn, slug, "LS")
        conn.execute("DELETE FROM velora.inbox_events WHERE tenant_id = %s", (ids["tenants"],))
        conn.execute("DELETE FROM velora.outbox_messages WHERE tenant_id = %s", (ids["tenants"],))
    return Shop(TenantId(ids["tenants"]), ids["channels"], f"session-{slug}")


class ScopedDispatcher(Dispatcher):
    """Real discovery, filtered to this test's tenants, so a worker round never touches the
    tenants other tests share."""

    def __init__(self, inner: Dispatcher, *shops: Shop) -> None:
        self._inner = inner
        self._allowed = {s.tenant for s in shops}

    async def tenants_with_work(self, inbox_event_types: Sequence[str]) -> list[TenantId]:
        found = await self._inner.tenants_with_work(inbox_event_types)
        return [t for t in found if t in self._allowed]

    async def tenants_with_channels(self) -> list[TenantId]:
        return [t for t in await self._inner.tenants_with_channels() if t in self._allowed]


@pytest.fixture()
def shop(db: PgDatabase) -> Shop:
    return new_shop(db)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture()
def fake() -> FakeProvider:
    return FakeProvider()


@asynccontextmanager
async def open_env(pg: PgDatabase, clock: FakeClock, fake: FakeProvider) -> AsyncIterator[Env]:
    app_url = pg.cluster.url(pg.name, APP_ROLE)
    dispatcher_url = pg.cluster.url(pg.name, DISPATCHER_ROLE)
    async with (
        AsyncConnectionPool(app_url, open=False, min_size=1, max_size=4) as app_pool,
        AsyncConnectionPool(dispatcher_url, open=False, min_size=1, max_size=1) as d_pool,
    ):
        yield Env(Database(app_pool), Dispatcher(d_pool, clock), clock, fake)


Scenario = Callable[[Env], Awaitable[Any]]


@pytest.fixture()
def run(
    db: PgDatabase, clock: FakeClock, fake: FakeProvider
) -> Iterator[Callable[[Scenario], Any]]:
    def runner(scenario: Scenario) -> Any:
        async def main() -> Any:
            async with open_env(db, clock, fake) as env:
                return await scenario(env)

        loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        return asyncio.run(main(), loop_factory=loop)

    yield runner


def rows(pg: PgDatabase, query: str, *params: Any) -> list[tuple[Any, ...]]:
    """Read as the administrator (bypasses RLS), for assertions."""
    with pg.admin() as conn:
        return conn.execute(query.encode(), params).fetchall()

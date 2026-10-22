"""Fixtures for the P10 monitoring tests.

The monitor reads across every tenant, so these tests get a database of their own (one per
test module): counts are then exact, and no other test's tenants show up in a snapshot.
Scenarios run like the messaging tests (`run`), with app_rw, velora_dispatcher and
velora_monitor connections, the `FakeProvider` and a simulated clock.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import AsyncConnection

from tests.db import conftest as db_fixtures
from tests.db.conftest import MONITOR_ROLE, OWNER_ROLE, Cluster, PgDatabase
from tests.messaging.conftest import Env, open_env
from velora.clock import FakeClock
from velora.db.migrate import migrate_up
from velora.messaging.providers.fake import FakeProvider
from velora.monitoring.notify import Notification, Notifier, NotifyError

cluster = db_fixtures.cluster

# 14:00 in Pakistan.
T0 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def mondb(cluster: Cluster) -> Iterator[PgDatabase]:
    name = db_fixtures.new_database_name("monitor")
    cluster.create_database(name)
    try:
        migrate_up(cluster.url(name, OWNER_ROLE))
        yield PgDatabase(cluster, name)
    finally:
        cluster.drop_database(name)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture()
def fake() -> FakeProvider:
    return FakeProvider()


class RecordingNotifier(Notifier):
    """Keeps what it was given. `down` makes it refuse, like an unreachable mail server."""

    name = "recording"

    def __init__(self) -> None:
        self.sent: list[Notification] = []
        self.down = False

    async def send(self, notification: Notification) -> None:
        if self.down:
            raise NotifyError("mail server unreachable")
        self.sent.append(notification)

    def keys(self, state: str = "firing") -> list[str]:
        return [n.key for n in self.sent if n.state == state]


@dataclass
class MonEnv:
    env: Env
    url: str
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)

    async def connect(self) -> AsyncConnection[Any]:
        return await AsyncConnection.connect(self.url)


Scenario = Callable[[MonEnv], Awaitable[Any]]


@pytest.fixture()
def run(
    mondb: PgDatabase, clock: FakeClock, fake: FakeProvider
) -> Iterator[Callable[[Scenario], Any]]:
    def runner(scenario: Scenario) -> Any:
        async def main() -> Any:
            async with open_env(mondb, clock, fake) as env:
                return await scenario(MonEnv(env, mondb.cluster.url(mondb.name, MONITOR_ROLE)))

        loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        return asyncio.run(main(), loop_factory=loop)

    yield runner


@asynccontextmanager
async def monitor_conn(mon: MonEnv) -> AsyncIterator[AsyncConnection[Any]]:
    conn = await mon.connect()
    async with conn:
        yield conn

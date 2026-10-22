"""The local development tools (`python -m velora.dev`): the demo restaurant goes live through
the founder's onboarding tools, and the simulated customer's message is answered through the
real ingress, worker and outbox path."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from tests.db import conftest as db_fixtures
from tests.db.conftest import APP_ROLE, PLATFORM_ROLE, PgDatabase
from velora.admin.onboarding import Onboarding
from velora.clock import SystemClock
from velora.config import Settings
from velora.conversation.engine import ConversationEngine
from velora.db import Database
from velora.dev.customer import Customer
from velora.dev.demo import SESSION_REF, create_demo
from velora.messaging.channels import ChannelMonitor
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender
from velora.messaging.providers.fake import FakeProvider

cluster = db_fixtures.cluster
db = db_fixtures.db
LOOP = asyncio.SelectorEventLoop if sys.platform == "win32" else None


def _run[T](fn: Callable[[], Awaitable[T]]) -> T:
    """On a thread of its own: sync Playwright may own this thread's event loop."""

    async def main() -> T:
        return await fn()

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, main(), loop_factory=LOOP).result()


@pytest.fixture()
def platform(db: PgDatabase) -> Iterator[Onboarding]:
    with db.platform() as conn:
        yield Onboarding(conn, operator="test")


def test_the_demo_goes_live_and_the_simulated_customer_is_answered(
    db: PgDatabase, platform: Onboarding
) -> None:
    settings = Settings(
        env="development", log_level="INFO", public_base_url="http://127.0.0.1:8000"
    )
    demo = _run(
        lambda: create_demo(
            platform,
            db.cluster.url(db.name, PLATFORM_ROLE),
            operator="test",
            magic_link_url=settings.magic_link_url,
        )
    )
    assert demo.owner_link.startswith("http://127.0.0.1:8000/login#")
    assert platform.go_live_gaps(demo.tenant) == []
    with db.admin() as conn:
        status: Any = conn.execute(
            "SELECT t.status, (SELECT count(*) FROM velora.menu_versions v "
            "WHERE v.tenant_id = t.id AND v.frozen_at IS NULL) FROM velora.tenants t "
            "WHERE t.id = %s",
            (demo.tenant.value,),
        ).fetchone()
    assert status == ("active", 1)  # live, with the sample menu waiting as a draft

    provider = FakeProvider()
    clock = SystemClock()

    async def chat() -> list[str]:
        async with AsyncConnectionPool(db.cluster.url(db.name, APP_ROLE), open=False) as pool:
            database = Database(pool)
            providers = {"fake": provider}
            await ChannelMonitor(database, providers, clock).poll(demo.tenant)
            customer = Customer(database, provider, clock, SESSION_REF, "+923001234567")
            await customer.say("hi")
            engine = ConversationEngine(clock, providers, None, ai_timeout=1)
            await InboxProcessor(database, clock, {"message": engine.handle}).run_once(demo.tenant)
            await OutboxSender(database, providers, clock).run_once(demo.tenant)
            return [m.text or "" for m in customer.new_replies()]

    replies = _run(chat)
    assert replies and "automated ordering assistant for Demo Kitchen" in replies[0]

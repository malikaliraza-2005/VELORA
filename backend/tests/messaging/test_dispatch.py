"""Cross-tenant work discovery (the AD-26 finding for P5).

`velora_dispatcher` may learn which tenants have due work, and nothing else: no payloads,
no tenant data, no writes. The resolver stays the only SECURITY DEFINER function
(tests/db/test_resolver.py). The work itself is done as app_rw, one tenant at a time.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from psycopg import errors

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env, ScopedDispatcher, new_shop
from tests.messaging.test_outbox import ALI, put
from velora.messaging.channels import ChannelMonitor
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender, apply_delivery_status
from velora.messaging.providers.fake import FakeProvider
from velora.workers.runner import Worker


def test_discovery_returns_only_tenants_with_due_work(run: Any, db: PgDatabase) -> None:
    due, later, held, idle = (new_shop(db) for _ in range(4))

    async def scenario(env: Env) -> list[Any]:
        await put(env, due, ALI, "now")
        await put(env, later, ALI, "later")
        await put(env, held, ALI, "held")
        with db.admin() as conn:
            conn.execute(
                "UPDATE velora.outbox_messages SET next_attempt_at = %s WHERE tenant_id = %s",
                (env.clock.now() + timedelta(minutes=5), later.tenant.value),
            )
            conn.execute(
                "UPDATE velora.outbox_messages SET status = 'waiting_for_channel' "
                "WHERE tenant_id = %s",
                (held.tenant.value,),
            )
        return await env.dispatcher.tenants_with_work(["message"])

    found = set(run(scenario))
    assert due.tenant in found and held.tenant in found
    assert later.tenant not in found and idle.tenant not in found


@pytest.mark.parametrize(
    "query",
    [
        "SELECT payload FROM velora.inbox_events",
        "SELECT payload FROM velora.outbox_messages",
        "SELECT session_ref FROM velora.channels",
        "SELECT * FROM velora.tenants",
        "SELECT * FROM velora.customers",
        "SELECT * FROM velora.orders",
        "SELECT * FROM velora.audit_log",
        "UPDATE velora.outbox_messages SET status = 'cancelled'",
        "INSERT INTO velora.global_pause_events (paused, reason, operator) VALUES (true, 'x', 'd')",
    ],
)
def test_the_dispatcher_sees_no_content_and_writes_nothing(db: PgDatabase, query: str) -> None:
    with db.dispatcher() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(query.encode())


def test_the_dispatcher_role_is_not_privileged(db: PgDatabase) -> None:
    with db.admin() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolinherit FROM pg_roles "
            "WHERE rolname = 'velora_dispatcher'"
        ).fetchone()
    assert row == (False, False, False)


def test_a_worker_round_serves_every_tenant_with_work(
    run: Any, db: PgDatabase, fake: FakeProvider
) -> None:
    shops = [new_shop(db), new_shop(db)]

    async def scenario(env: Env) -> None:
        for s in shops:
            await put(env, s, ALI, f"hello from {s.session_ref}")
        worker = Worker(
            ScopedDispatcher(env.dispatcher, *shops),
            InboxProcessor(env.db, env.clock, {"delivery_status": apply_delivery_status}),
            OutboxSender(env.db, env.providers, env.clock),
            ChannelMonitor(env.db, env.providers, env.clock),
            env.clock,
        )
        await worker.run_round()

    run(scenario)
    assert sorted(m.session_ref for m in fake.sent) == sorted(s.session_ref for s in shops)


def test_one_tenants_failure_does_not_stop_the_others(
    run: Any, db: PgDatabase, fake: FakeProvider
) -> None:
    broken, fine = new_shop(db), new_shop(db)

    class Flaky(OutboxSender):
        async def run_once(self, tenant: Any) -> Any:
            if tenant == broken.tenant:
                raise RuntimeError("database hiccup password=hunter2hunter2")
            return await super().run_once(tenant)

    async def scenario(env: Env) -> None:
        await put(env, broken, ALI, "lost?")
        await put(env, fine, ALI, "delivered")
        worker = Worker(
            ScopedDispatcher(env.dispatcher, broken, fine),
            InboxProcessor(env.db, env.clock, {}),
            Flaky(env.db, env.providers, env.clock),
            ChannelMonitor(env.db, env.providers, env.clock),
            env.clock,
        )
        await worker.run_round()

    run(scenario)
    assert [m.text for m in fake.sent] == ["delivered"]

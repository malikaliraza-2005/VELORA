"""AD-38: data retention. Old messages, confirmations, carts, delivery addresses and inactive
customers are removed on schedule; orders and amounts stay; a customer can be forgotten on
request; the worker sweeps once an hour."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.db.conftest import APP_ROLE, PLATFORM_ROLE, PgDatabase
from tests.messaging.conftest import Env
from tests.orders.conftest import ADDRESS, Customer, Shop, chat, ids_of, orders_of, rows, to_summary
from velora.clock import FakeClock
from velora.db import TenantId
from velora.messaging.outbox import OutboxSender
from velora.retention import (
    REDACTED,
    ForgetRefusedError,
    Retention,
    RetentionPolicy,
    RetentionReport,
)
from velora.retention.__main__ import main
from velora.workers.runner import RETENTION_INTERVAL, Worker

# Some rows take the database's own clock (now()), not the test clock: jumps are generous.
DAY = timedelta(days=1)


def complete(pg: PgDatabase, order_id: uuid.UUID) -> None:
    """Move a delivery COD order through to completed, as staff would."""
    with pg.admin() as conn:
        for status in ("accepted", "preparing", "out_for_delivery", "completed"):
            conn.execute("UPDATE velora.orders SET status = %s WHERE id = %s", (status, order_id))


async def place(env: Env, pg: PgDatabase, shop: Shop) -> tuple[Customer, uuid.UUID]:
    customer = chat(env, pg, shop)
    await to_summary(customer)
    await customer.send("YES")
    ((order_id, *_),) = orders_of(pg, customer)
    return customer, order_id


async def flush(env: Env, shop: Shop) -> None:
    await OutboxSender(env.db, env.providers, env.clock).run_once(shop.tenant)


def queue_rows(pg: PgDatabase, shop: Shop) -> list[tuple[Any, ...]]:
    return rows(
        pg,
        "SELECT 'in', payload, ordering_key FROM velora.inbox_events WHERE tenant_id = %s "
        "UNION ALL SELECT 'out', payload, ordering_key FROM velora.outbox_messages "
        "WHERE tenant_id = %s",
        shop.tenant.value,
        shop.tenant.value,
    )


def test_old_personal_data_is_removed_on_schedule(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        retention = Retention(env.db, clock)
        buyer, order_id = await place(env, db, shop)
        complete(db, order_id)
        # A second customer stops at the summary: an open cart, an address, a confirmation.
        browser = chat(env, db, shop)
        await to_summary(browser)
        await flush(env, shop)
        before = queue_rows(db, shop)
        assert before and all(payload != REDACTED for _, payload, _ in before)

        clock.advance(29 * DAY)
        assert (await retention.sweep(shop.tenant)).total == 0  # nothing is old enough yet

        clock.advance(2 * DAY)
        report = await retention.sweep(shop.tenant)
        assert report.inbox == len([r for r in before if r[0] == "in"])
        assert report.outbox == len([r for r in before if r[0] == "out"])
        assert all(p == REDACTED and key is None for _, p, key in queue_rows(db, shop))
        assert report.confirmations == 2  # the used one and the expired one
        mine = [ids_of(db, c)[1] for c in (buyer, browser)]
        assert all(
            p == REDACTED
            for (p,) in rows(
                db,
                "SELECT payload FROM velora.pending_actions WHERE customer_id = ANY(%s)",
                mine,
            )
        )
        state, context, _, _ = browser.conversation()
        assert (state, context) == ("idle", {})
        # The order stays whole: amounts, items and, for now, the address.
        ((total, address, items),) = rows(
            db,
            "SELECT o.total_paisa, o.delivery_address, count(i.*) FROM velora.orders o "
            "JOIN velora.order_items i ON i.order_id = o.id WHERE o.id = %s "
            "GROUP BY o.id",
            order_id,
        )
        assert total > 0 and address == ADDRESS and items == 1
        assert (await retention.sweep(shop.tenant)).total == 0  # each row once

        # A reset conversation works like a new one.
        assert await browser.send("menu")

        clock.advance(100 * DAY)
        assert (await retention.sweep(shop.tenant)).addresses == 1
        ((address, note),) = rows(
            db, "SELECT delivery_address, delivery_note FROM velora.orders WHERE id = %s", order_id
        )
        assert (address, note) == (None, None)

        clock.advance(400 * DAY)
        while (report := await retention.sweep(shop.tenant)).total:
            pass
        _, customer_id = ids_of_anonymised(db, order_id)
        (phone, name, anonymised) = rows(
            db,
            "SELECT phone_e164, display_name, anonymised_at FROM velora.customers WHERE id = %s",
            customer_id,
        )[0]
        assert phone is None and name is None and anonymised is not None
        assert orders_of(db, buyer) == []  # no longer findable by the number
        assert rows(db, "SELECT status FROM velora.orders WHERE id = %s", order_id) == [
            ("completed",)
        ]

    run(scenario)


def ids_of_anonymised(pg: PgDatabase, order_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    ((conversation, customer),) = rows(
        pg, "SELECT conversation_id, customer_id FROM velora.orders WHERE id = %s", order_id
    )
    return conversation, customer


def test_a_customer_with_an_open_order_is_kept(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        buyer, order_id = await place(env, db, shop)  # cod_confirmed: still open
        clock.advance(500 * DAY)
        retention = Retention(env.db, clock)
        while (await retention.sweep(shop.tenant)).total:
            pass
        assert [status for _, status, *_ in orders_of(db, buyer)] == ["cod_confirmed"]
        assert rows(db, "SELECT delivery_address FROM velora.orders WHERE id = %s", order_id) == [
            (ADDRESS,)
        ]

    run(scenario)


def test_sweeps_work_in_batches(run: Any, db: PgDatabase, shop: Shop, clock: FakeClock) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("menu", "1", "menu"):
            await customer.send(text)
        await flush(env, shop)
        clock.advance(31 * DAY)
        retention = Retention(env.db, clock, batch=1)
        first = await retention.sweep(shop.tenant)
        assert (first.inbox, first.outbox) == (1, 1)
        while (await retention.sweep(shop.tenant)).total:
            pass
        assert all(p == REDACTED for _, p, _ in queue_rows(db, shop))

    run(scenario)


def test_a_customer_can_be_forgotten_on_request(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await flush(env, shop)
        conversation_id, customer_id = ids_of(db, customer)
        await customer.deliver("are you there?")  # still waiting in the inbox
        report = await Retention(env.db, clock).forget(
            shop.tenant, customer.phone, operator="founder"
        )
        assert report.customers == 1 and report.inbox >= 1 and report.outbox >= 1
        assert all(p == REDACTED and key is None for _, p, key in queue_rows(db, shop))
        ((voided, payload),) = rows(
            db,
            "SELECT voided_at, payload FROM velora.pending_actions WHERE customer_id = %s",
            customer_id,
        )
        assert voided is not None and payload == REDACTED
        assert rows(
            db, "SELECT state, context FROM velora.conversations WHERE id = %s", conversation_id
        ) == [("idle", {})]
        ((phone, anonymised),) = rows(
            db, "SELECT phone_e164, anonymised_at FROM velora.customers WHERE id = %s", customer_id
        )
        assert phone is None and anonymised is not None
        ((action, details),) = rows(
            db,
            "SELECT action, details FROM velora.audit_log WHERE target_id = %s",
            customer_id,
        )
        assert action == "customer.forgotten" and details["operator"] == "founder"
        assert customer.phone not in str(details)

        # Writing again later starts afresh, as a new customer; the old summary is gone.
        replies = await customer.send("YES")
        assert replies and orders_of(db, customer) == []

    run(scenario)


def test_forgetting_is_refused_while_an_order_is_open(
    run: Any, db: PgDatabase, shop: Shop, clock: FakeClock
) -> None:
    async def scenario(env: Env) -> None:
        buyer, _ = await place(env, db, shop)
        retention = Retention(env.db, clock)
        with pytest.raises(ForgetRefusedError, match="in progress"):
            await retention.forget(shop.tenant, buyer.phone, operator="founder")
        with pytest.raises(ForgetRefusedError, match="no customer"):
            await retention.forget(shop.tenant, "+923009999999", operator="founder")
        with pytest.raises(ForgetRefusedError, match="E.164"):
            await retention.forget(shop.tenant, "03001234567", operator="founder")

    run(scenario)


def test_forget_command_line(
    run: Any,
    db: PgDatabase,
    shop: Shop,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def scenario(env: Env) -> str:
        customer = chat(env, db, shop)
        await customer.send("menu")
        return customer.phone

    phone = run(scenario)
    (slug,) = rows(db, "SELECT slug FROM velora.tenants WHERE id = %s", shop.tenant.value)[0]
    monkeypatch.setenv("VELORA_DATABASE_URL", db.cluster.url(db.name, APP_ROLE))
    monkeypatch.setenv("VELORA_PLATFORM_DATABASE_URL", db.cluster.url(db.name, PLATFORM_ROLE))
    args = ["forget", "--tenant", slug, "--phone", phone]
    assert main(args) == 2  # no operator name
    assert main(["--operator", "founder", *args]) == 0
    assert f"customer forgotten at {slug}" in capsys.readouterr().out
    assert main(["--operator", "founder", *args]) == 1  # already forgotten
    assert "no customer" in capsys.readouterr().err


class CountingRetention(Retention):
    def __init__(self) -> None:
        self.swept: list[TenantId] = []

    async def sweep(self, tenant: TenantId) -> RetentionReport:
        self.swept.append(tenant)
        return RetentionReport()


class OneTenant:
    def __init__(self, tenant: TenantId) -> None:
        self.tenant = tenant

    async def tenants_with_channels(self) -> list[TenantId]:
        return []

    async def tenants_with_work(self, types: Any) -> list[TenantId]:
        return []

    async def tenants_for_recovery(self) -> list[TenantId]:
        return [self.tenant]


def test_the_worker_sweeps_every_tenant_once_an_hour() -> None:
    import asyncio

    clock = FakeClock(datetime(2026, 10, 24, 9, 0, tzinfo=UTC))
    tenant = TenantId(uuid.uuid4())
    retention = CountingRetention()
    inbox = type("Inbox", (), {"event_types": ("message",)})()
    worker = Worker(
        OneTenant(tenant),  # type: ignore[arg-type]
        inbox,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        clock,
        health_interval=timedelta(days=1),
        retention=retention,
    )

    async def rounds() -> None:
        worker._last_poll = clock.now()  # skip the health poll
        await worker.run_round()
        clock.advance(RETENTION_INTERVAL / 2)
        await worker.run_round()
        clock.advance(RETENTION_INTERVAL / 2)
        await worker.run_round()

    asyncio.run(rounds())
    assert retention.swept == [tenant, tenant]


def test_the_default_policy() -> None:
    policy = RetentionPolicy()
    assert (policy.messages, policy.confirmations, policy.conversations) == (30 * DAY,) * 3
    assert (policy.delivery_addresses, policy.customers) == (90 * DAY, 365 * DAY)

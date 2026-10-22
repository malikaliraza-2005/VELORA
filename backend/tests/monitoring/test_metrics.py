"""P10-T2 and T4: each metric changes when its condition is simulated, and the daily pilot
metrics count what P13 needs.

Snapshots cover the whole module database, so each test compares before and after.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from tests.db.conftest import PgDatabase
from tests.db.seed import seed_tenant
from tests.messaging.conftest import Shop, new_shop
from tests.monitoring.conftest import T0, MonEnv, monitor_conn
from velora.conversation.pktime import PKT
from velora.db import TenantId
from velora.messaging.channels import ChannelMonitor, record_state
from velora.messaging.inbox import InboxEvent, InboxProcessor, Ingress
from velora.messaging.outbox import OutboundText, OutboxSender, enqueue
from velora.messaging.provider import InboundMessage, PermanentSendError
from velora.monitoring.daily import collect_daily, time_in_state
from velora.monitoring.metrics import Snapshot, collect, record_backup

DAY = date(2026, 10, 21)  # T0 is 14:00 that day in Pakistan


async def snap(mon: MonEnv) -> Snapshot:
    async with monitor_conn(mon) as conn:
        return await collect(conn, mon.env.clock.now())


def inbound(shop: Shop, key: str) -> InboundMessage:
    return InboundMessage(
        session_ref=shop.session_ref,
        dedup_key=key,
        provider_message_id=key,
        sender="+923000000101",
        chat_kind="direct",
        kind="text",
        text="hi",
        received_at=T0,
    )


async def receive(mon: MonEnv, shop: Shop, key: str) -> None:
    await Ingress(mon.env.db, mon.env.clock).receive("fake", shop.session_ref, [inbound(shop, key)])


async def failing(conn: AsyncConnection[Any], tenant: TenantId, event: InboxEvent) -> None:
    raise RuntimeError("handler broke")


async def queue_text(mon: MonEnv, shop: Shop, key: str) -> None:
    async with mon.env.db.tenant(shop.tenant) as conn:
        await enqueue(
            conn,
            shop.tenant,
            channel_id=shop.channel_id,
            message=OutboundText("+923000000101", "hello"),
            idempotency_key=key,
            purpose="status_update",
            at=mon.env.clock.now(),
        )


def add_order(
    pg: PgDatabase, shop: Shop, status: str, at: datetime, *, handoff_at: datetime | None = None
) -> uuid.UUID:
    """An order placed at `at`, by a new customer. With `handoff_at`, the chat was handed to
    staff then (the trigger records it)."""
    with pg.admin() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(shop.tenant.value),))
        phone = f"+92300{uuid.uuid4().int % 10**7:07d}"
        (customer,) = conn.execute(
            "INSERT INTO velora.customers (tenant_id, phone_e164) VALUES (%s, %s) RETURNING id",
            (shop.tenant.value, phone),
        ).fetchone() or (None,)
        (conversation,) = conn.execute(
            "INSERT INTO velora.conversations (tenant_id, channel_id, customer_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (shop.tenant.value, shop.channel_id, customer),
        ).fetchone() or (None,)
        if handoff_at is not None:
            conn.execute(
                "UPDATE velora.conversations SET state = 'handoff', handoff_at = %s, "
                "handoff_reason = 'requested' WHERE id = %s",
                (handoff_at, conversation),
            )
        (order,) = conn.execute(
            "INSERT INTO velora.orders (tenant_id, order_number, customer_id, conversation_id, "
            "status, fulfilment, payment_method, created_at) "
            "VALUES (%s, velora.next_order_number(), %s, %s, %s, 'takeaway', 'cod', %s) "
            "RETURNING id",
            (shop.tenant.value, customer, conversation, status, at),
        ).fetchone() or (None,)
    assert order is not None
    return uuid.UUID(str(order))


# Snapshot ---------------------------------------------------------------------------------


def test_inbox_queue_age_grows_while_events_wait(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> tuple[Snapshot, Snapshot, Snapshot]:
        before = await snap(mon)
        await receive(mon, shop, "age-1")
        mon.env.clock.advance(timedelta(minutes=7))
        waiting = await snap(mon)

        async def ok(conn: AsyncConnection[Any], tenant: TenantId, e: InboxEvent) -> None:
            return None

        await InboxProcessor(mon.env.db, mon.env.clock, {"message": ok}).run_once(shop.tenant)
        return before, waiting, await snap(mon)

    before, waiting, after = run(scenario)
    assert waiting.inbox_due == before.inbox_due + 1
    assert waiting.inbox_oldest_due is not None
    assert waiting.inbox_oldest_due >= timedelta(minutes=7)
    assert after.inbox_due == before.inbox_due


def test_a_failed_inbox_event_is_a_dead_letter(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> tuple[Snapshot, Snapshot]:
        before = await snap(mon)
        await receive(mon, shop, "dead-1")
        processor = InboxProcessor(mon.env.db, mon.env.clock, {"message": failing})
        for _ in range(7):
            await processor.run_once(shop.tenant)
            mon.env.clock.advance(timedelta(minutes=20))
        return before, await snap(mon)

    before, after = run(scenario)
    assert after.inbox_failed == before.inbox_failed + 1
    assert after.dead_letters == before.dead_letters + 1


def test_outbox_age_holds_and_send_failures_are_counted(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> list[Snapshot]:
        seen = [await snap(mon)]
        await queue_text(mon, shop, "out-1")
        mon.env.clock.advance(timedelta(minutes=6))
        seen.append(await snap(mon))
        mon.env.fake.fail_next(shop.session_ref, PermanentSendError("invalid recipient"))
        await OutboxSender(mon.env.db, mon.env.providers, mon.env.clock).run_once(shop.tenant)
        seen.append(await snap(mon))
        return seen

    before, waiting, failed = run(scenario)
    assert waiting.outbox_due == before.outbox_due + 1
    assert waiting.outbox_oldest_due is not None
    assert waiting.outbox_oldest_due >= timedelta(minutes=6)
    assert failed.outbox_due == before.outbox_due
    assert failed.outbox_failed == before.outbox_failed + 1


def test_messages_held_for_a_down_channel_are_counted(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb, state="needs_qr")

    async def scenario(mon: MonEnv) -> tuple[Snapshot, Snapshot]:
        before = await snap(mon)
        await queue_text(mon, shop, "held-1")
        await OutboxSender(mon.env.db, mon.env.providers, mon.env.clock).run_once(shop.tenant)
        return before, await snap(mon)

    before, after = run(scenario)
    assert after.outbox_waiting_for_channel == before.outbox_waiting_for_channel + 1


def test_channel_state_changes_and_time_in_needs_qr(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> tuple[Snapshot, Snapshot]:
        before = await snap(mon)
        mon.env.fake.set_state(shop.session_ref, "needs_qr")
        await ChannelMonitor(mon.env.db, mon.env.providers, mon.env.clock).poll(shop.tenant)
        mon.env.clock.advance(timedelta(minutes=9))
        return before, await snap(mon)

    before, after = run(scenario)
    assert after.channel_changes == before.channel_changes + 1
    (channel,) = [c for c in after.channels if c.channel_id == shop.channel_id]
    assert channel.state == "needs_qr"
    assert channel.since == T0
    assert channel.tenant.startswith("m-")


def test_refused_webhooks_are_counted_by_reason(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> tuple[Snapshot, list[bool], Snapshot, Snapshot]:
        before = await snap(mon)
        ingress = Ingress(mon.env.db, mon.env.clock)
        results = [
            await ingress.reject("fake", shop.session_ref, "signature"),
            await ingress.reject("fake", shop.session_ref, "signature"),
            await ingress.reject("fake", shop.session_ref, "payload"),
            await ingress.reject("fake", "no-such-session", "signature"),
        ]
        during = await snap(mon)
        mon.env.clock.advance(timedelta(minutes=16))
        return before, results, during, await snap(mon)

    before, results, during, later = run(scenario)
    assert results == [True, True, True, False]
    assert during.ingress_rejections.get("signature", 0) == (
        before.ingress_rejections.get("signature", 0) + 2
    )
    assert during.ingress_rejections.get("payload", 0) >= 1
    assert later.ingress_rejections == {}


def test_backup_runs_are_reported(run: Any) -> None:
    async def scenario(mon: MonEnv) -> list[Snapshot]:
        seen = []
        async with monitor_conn(mon) as conn:
            await record_backup(conn, ok=True, kind="hourly-dump", detail=None, at=T0)
        seen.append(await snap(mon))
        async with monitor_conn(mon) as conn:
            await record_backup(
                conn,
                ok=False,
                kind="hourly-dump",
                detail="upload refused",
                at=T0 + timedelta(hours=1),
            )
        mon.env.clock.advance(timedelta(hours=1))
        seen.append(await snap(mon))
        return seen

    good, bad = run(scenario)
    assert good.last_backup is not None and good.last_backup.ok
    assert bad.last_backup is not None and not bad.last_backup.ok
    assert bad.last_backup.detail == "upload refused"
    assert bad.last_good_backup == T0


def test_the_order_funnel_counts_todays_orders_by_stage(run: Any, mondb: PgDatabase) -> None:
    shop = new_shop(mondb)

    async def scenario(mon: MonEnv) -> Snapshot:
        return await snap(mon)

    before = run(scenario)
    for status in ("cod_confirmed", "preparing", "completed", "completed", "cancelled", "expired"):
        add_order(mondb, shop, status, T0 - timedelta(hours=1))
    add_order(mondb, shop, "completed", T0 - timedelta(days=1))  # yesterday: not counted
    after = run(scenario).funnel
    assert after.placed == before.funnel.placed + 6
    assert after.awaiting == before.funnel.awaiting + 1
    assert after.in_progress == before.funnel.in_progress + 1
    assert after.completed == before.funnel.completed + 2
    assert after.cancelled == before.funnel.cancelled + 1
    assert after.expired == before.funnel.expired + 1


def test_the_global_pause_shows(run: Any, mondb: PgDatabase) -> None:
    async def scenario(mon: MonEnv) -> Snapshot:
        return await snap(mon)

    with mondb.admin() as conn:
        conn.execute(
            "INSERT INTO velora.global_pause_events (paused, reason, operator) "
            "VALUES (true, 'drill', 'test')"
        )
    paused = run(scenario)
    with mondb.admin() as conn:
        conn.execute(
            "INSERT INTO velora.global_pause_events (paused, operator) VALUES (false, 'test')"
        )
    assert paused.global_pause == "drill"
    assert run(scenario).global_pause is None


# Handoff records --------------------------------------------------------------------------


def test_each_handoff_is_recorded_once(mondb: PgDatabase) -> None:
    shop = new_shop(mondb)
    add_order(mondb, shop, "completed", T0, handoff_at=T0 - timedelta(minutes=5))
    with mondb.admin() as conn:
        rows = conn.execute(
            "SELECT reason, created_at FROM velora.handoff_events WHERE tenant_id = %s",
            (shop.tenant.value,),
        ).fetchall()
        # Another state change during the same handoff isn't a new one.
        conn.execute(
            "UPDATE velora.conversations SET state_version = state_version + 1 "
            "WHERE tenant_id = %s",
            (shop.tenant.value,),
        )
        again = conn.execute(
            "SELECT count(*) FROM velora.handoff_events WHERE tenant_id = %s",
            (shop.tenant.value,),
        ).fetchone()
    assert rows == [("requested", T0 - timedelta(minutes=5))]
    assert again == (1,)


# Daily pilot metrics ----------------------------------------------------------------------


def test_time_in_state_adds_up_the_intervals() -> None:
    start = T0
    end = T0 + timedelta(hours=10)
    changes = [
        (T0 + timedelta(hours=1), "needs_qr"),
        (T0 + timedelta(hours=2), "connected"),
        (T0 + timedelta(hours=9), "needs_qr"),
    ]
    assert time_in_state("connected", changes, start, end, "needs_qr") == timedelta(hours=2)
    assert time_in_state("needs_qr", [], start, end, "needs_qr") == timedelta(hours=10)
    assert time_in_state("connected", [], start, end, "needs_qr") == timedelta(0)


def test_daily_metrics_per_restaurant(run: Any, mondb: PgDatabase) -> None:
    shop, quiet = new_shop(mondb), new_shop(mondb)
    morning = T0 - timedelta(hours=4)
    add_order(mondb, shop, "completed", morning)
    add_order(mondb, shop, "completed", morning, handoff_at=morning - timedelta(minutes=10))
    add_order(mondb, shop, "cancelled", morning)
    add_order(mondb, shop, "cod_confirmed", morning)

    async def scenario(mon: MonEnv) -> Any:
        async with mon.env.db.tenant(shop.tenant) as conn:
            await record_state(conn, shop.tenant, shop.channel_id, "needs_qr", at=T0, source="poll")
            await record_state(
                conn,
                shop.tenant,
                shop.channel_id,
                "connected",
                at=T0 + timedelta(minutes=30),
                source="poll",
            )
        await queue_text(mon, shop, "daily-dead")
        mon.env.fake.fail_next(shop.session_ref, PermanentSendError("invalid recipient"))
        await OutboxSender(mon.env.db, mon.env.providers, mon.env.clock).run_once(shop.tenant)
        async with monitor_conn(mon) as conn:
            return await collect_daily(conn, DAY, T0 + timedelta(hours=2))

    report = run(scenario)
    rows = {r.tenant_id: r for r in report.tenants}
    row = rows[shop.tenant.value]
    assert (row.orders, row.completed, row.completed_without_handoff) == (4, 2, 1)
    assert (row.cancelled, row.handoffs) == (1, 1)
    assert (row.disconnects, row.needs_qr_minutes) == (1, 30)
    assert row.dead_letters == 1
    assert rows[quiet.tenant.value].orders == 0
    assert report.starts_at.isoformat() == "2026-10-21T00:00:00+05:00"


def test_daily_metrics_count_payment_verifications(run: Any, mondb: PgDatabase) -> None:
    with mondb.admin() as conn:
        tenant = seed_tenant(conn, f"pay-{uuid.uuid4().hex[:8]}", "PV")["tenants"]
    now = datetime.now(UTC)

    async def scenario(mon: MonEnv) -> Any:
        async with monitor_conn(mon) as conn:
            return await collect_daily(conn, now.astimezone(PKT).date(), now)

    (row,) = [r for r in run(scenario).tenants if r.tenant_id == tenant]
    assert (row.payments_verified, row.payments_reversed) == (1, 0)

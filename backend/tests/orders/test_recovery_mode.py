"""P11-T4 and P11-T5: recovery mode on startup, and the recovery-mode drill.

Each of the nine steps is tested on its own, then the drill runs the real worker through a
simulated 1-hour outage. Exit criterion: **no duplicate orders, no stale YES accepted,
every outbox message resolved.**

Outages are simulated: the worker's heartbeat says it was alive until a moment, then the
simulated clock moves on. Each test uses its own heartbeat name, so tests never see each
other's outages.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from tests.db.conftest import APP_ROLE, PgDatabase, new_database_name
from tests.messaging.conftest import Env, ScopedDispatcher
from tests.orders.conftest import Customer, Shop, chat, orders_of, rows, to_summary
from velora.conversation import copy
from velora.db import Database, TenantId
from velora.identity.permissions import Role
from velora.identity.sessions import StaffSession
from velora.identity.tokens import new_token
from velora.messaging.channels import ChannelMonitor
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import (
    WE_ARE_BACK_TEXT,
    OutboxSender,
    RateCap,
    apply_delivery_status,
)
from velora.orders.service import OrderService
from velora.orders.timers import OrderTimers
from velora.workers.recovery import (
    HEARTBEAT_INTERVAL,
    Heartbeat,
    InboundReconciler,
    Recovery,
    RecoveryError,
    WorkerAlreadyRunningError,
    hold_worker_lock,
    recover_tenant,
)
from velora.workers.runner import Worker

HOUR = timedelta(hours=1)


def heartbeat(env: Env) -> Heartbeat:
    return Heartbeat(env.pool, name=f"t{uuid.uuid4().hex[:12]}")


def recovery(
    env: Env, beat: Heartbeat, *shops: Shop, reconcile: InboundReconciler | None = None
) -> Recovery:
    return Recovery(
        env.db,
        env.pool,
        ScopedDispatcher(env.dispatcher, *shops),
        env.clock,
        heartbeat=beat,
        reconcile=reconcile,
    )


async def outage(env: Env, beat: Heartbeat, length: timedelta) -> datetime:
    """The worker was alive until now, then stopped for `length`. Returns the outage start
    recovery will use."""
    now = env.clock.now()
    await beat.beat(started_at=now, at=now)
    env.clock.advance(length)
    return now + HEARTBEAT_INTERVAL


def recovery_row(pg: PgDatabase, shop: Shop, outage_started_at: datetime | None) -> dict[str, Any]:
    with pg.admin() as conn:
        cur = conn.execute(
            "SELECT * FROM velora.recovery_runs WHERE tenant_id = %s "
            "AND outage_started_at IS NOT DISTINCT FROM %s::timestamptz",
            (shop.tenant.value, outage_started_at),
        )
        row = cur.fetchone()
        assert row is not None and cur.description is not None
        return {d.name: v for d, v in zip(cur.description, row, strict=True)}


def manager(pg: PgDatabase, shop: Shop) -> StaffSession:
    found = rows(
        pg, "SELECT id FROM velora.staff_members WHERE tenant_id = %s LIMIT 1", shop.tenant.value
    )
    return StaffSession(
        tenant=shop.tenant,
        session_id=uuid.uuid4(),
        staff_id=found[0][0],
        role=Role.MANAGER,
        display_name="Manager",
        token=new_token(shop.tenant),
    )


def outbox_of(pg: PgDatabase, customer: Customer) -> list[tuple[str, str]]:
    """(status, text) of every message queued for a customer, in order."""
    return [
        (r[0], r[1])
        for r in rows(
            pg,
            "SELECT status, payload->>'text' FROM velora.outbox_messages "
            "WHERE tenant_id = %s AND ordering_key = %s ORDER BY seq",
            customer.shop.tenant.value,
            customer.phone,
        )
    ]


async def place(customer: Customer, *, payment: str = "cod") -> None:
    await to_summary(customer, payment=payment)
    (placed,) = await customer.send("YES")
    assert placed.startswith("✅ Order"), placed


# Step 1: health check --------------------------------------------------------------------


class DeadPool:
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[Any]]:
        raise OSError("connection refused")
        yield  # pragma: no cover


def test_step1_a_database_that_does_not_answer_stops_the_start(run: Any) -> None:
    async def scenario(env: Env) -> None:
        dead = DeadPool()
        start = Recovery(Database(dead), dead, env.dispatcher, env.clock)
        with pytest.raises(RecoveryError, match="does not answer"):
            await start.run()

    run(scenario)


def test_step1_an_unmigrated_database_stops_the_start(run: Any, db: PgDatabase) -> None:
    name = new_database_name("unmigrated")
    db.cluster.create_database(name)
    try:

        async def scenario(env: Env) -> None:
            url = db.cluster.url(name)
            async with AsyncConnectionPool(url, open=False, min_size=1, max_size=1) as pool:
                start = Recovery(Database(pool), pool, env.dispatcher, env.clock)
                with pytest.raises(RecoveryError, match="migrate up"):
                    await start.run()

        run(scenario)
    finally:
        db.cluster.drop_database(name)


def test_only_one_worker_runs_at_a_time(run: Any, db: PgDatabase) -> None:
    url = db.cluster.url(db.name, APP_ROLE)

    async def scenario(env: Env) -> None:
        async with hold_worker_lock(url):
            with pytest.raises(WorkerAlreadyRunningError):
                async with hold_worker_lock(url):
                    pass  # pragma: no cover
        async with hold_worker_lock(url):  # free again once the first worker is gone
            pass

    run(scenario)


def test_a_first_start_has_no_outage_and_voids_nothing(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await to_summary(customer)
        report = await recovery(env, beat, shop).run()
        assert report.outage_started_at is None and report.outage is None
        (result,) = report.tenants
        assert (result.nonces_voided, result.windows_extended) == (0, 0)
        assert customer.new_replies() == []
        # The next start knows when the worker was last alive.
        assert await beat.last_seen() == report.started_at

    run(scenario)


# Step 2: leases ----------------------------------------------------------------------------


def test_step2_events_a_dead_worker_was_processing_are_processed_once(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await customer.deliver("menu")
        claimed = await customer.processor().claim(shop.tenant)  # the worker dies here
        assert claimed is not None
        start = await outage(env, beat, timedelta(seconds=30))  # its lease hasn't ended
        await recovery(env, beat, shop).run()
        assert recovery_row(db, shop, start)["leases_reclaimed"] == 1
        assert rows(
            db, "SELECT status, claim_token FROM velora.inbox_events WHERE id = %s", claimed.id
        ) == [("pending", None)]
        assert await customer.processor().run_once(shop.tenant) == 1
        (menu,) = customer.new_replies()
        assert "1." in menu

    run(scenario)


# Step 3: inbound reconciliation (V-04) -----------------------------------------------------


def test_step3_inbound_reconciliation_is_a_hook_skipped_until_v04(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    calls: list[tuple[TenantId, datetime, datetime]] = []

    async def reconcile(
        conn: AsyncConnection[Any], tenant: TenantId, since: datetime, now: datetime
    ) -> int:
        calls.append((tenant, since, now))
        return 3

    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        first = await outage(env, beat, HOUR)
        await recovery(env, beat, shop).run()
        row = recovery_row(db, shop, first)
        assert (row["inbound"], row["inbound_recovered"]) == ("skipped", 0)
        second = await outage(env, beat, HOUR)
        await recovery(env, beat, shop, reconcile=reconcile).run()
        row = recovery_row(db, shop, second)
        assert (row["inbound"], row["inbound_recovered"]) == ("reconciled", 3)
        assert calls == [(shop.tenant, second, env.clock.now())]

    run(scenario)


# Step 4: confirmations issued before the outage --------------------------------------------


def test_step4_a_yes_on_a_summary_from_before_the_outage_places_nothing(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await to_summary(customer)
        start = await outage(env, beat, HOUR)
        await recovery(env, beat, shop).run()
        assert recovery_row(db, shop, start)["nonces_voided"] == 1
        # One apology, not a second "we're back" for the stale summary.
        assert customer.new_replies() == [copy.RECONFIRM_AFTER_OUTAGE]
        (again,) = await customer.send("YES")
        assert again.startswith(copy.SUMMARY_UPDATED)
        assert orders_of(db, customer) == []

    run(scenario)


# Step 5: transfer windows ------------------------------------------------------------------


def test_step5_transfer_windows_open_at_the_outage_are_extended_by_its_length(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        late, waiting = chat(env, db, shop), chat(env, db, shop)
        await place(late, payment="transfer")
        env.clock.advance(timedelta(minutes=31))  # its 30-minute window has passed
        await place(waiting, payment="transfer")
        (_, _, _, _, late_due) = orders_of(db, late)[0]
        (_, _, _, _, due) = orders_of(db, waiting)[0]
        start = await outage(env, beat, HOUR)
        report = await recovery(env, beat, shop).run()
        assert recovery_row(db, shop, start)["windows_extended"] == 1
        assert report.outage is not None
        assert orders_of(db, waiting)[0][4] == due + report.outage
        assert orders_of(db, late)[0][4] == late_due
        # The order whose window closed before the outage expires as usual; the other waits.
        await OrderTimers(env.db, env.clock).run_once(shop.tenant)
        assert orders_of(db, late)[0][1] == "expired"
        assert orders_of(db, waiting)[0][1] == "awaiting_transfer"

    run(scenario)


# Step 6: sends in flight -------------------------------------------------------------------


def test_step6_a_send_in_flight_becomes_unknown_and_is_never_resent(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await customer.send("menu")
        with db.admin() as conn:  # the worker dies mid-send
            conn.execute(
                "UPDATE velora.outbox_messages SET status = 'sending', "
                "claim_token = gen_random_uuid(), locked_until = %s "
                "WHERE tenant_id = %s AND ordering_key = %s",
                (env.clock.now() + timedelta(minutes=2), shop.tenant.value, customer.phone),
            )
        start = await outage(env, beat, timedelta(seconds=30))
        await recovery(env, beat, shop).run()
        assert recovery_row(db, shop, start)["messages_unknown"] == 1
        assert [status for status, _ in outbox_of(db, customer)] == ["unknown"]
        await OutboxSender(env.db, env.providers, env.clock).run_once(shop.tenant)
        assert env.fake.texts_to(customer.phone) == []

    run(scenario)


# Step 7: stale replies ---------------------------------------------------------------------


def test_step7_stale_replies_are_replaced_by_one_message_per_customer(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await customer.send("menu")
        await customer.send("1")
        start = await outage(env, beat, HOUR)
        await recovery(env, beat, shop).run()
        assert recovery_row(db, shop, start)["replies_stale"] == 2
        assert customer.new_replies() == [WE_ARE_BACK_TEXT]
        assert [status for status, _ in outbox_of(db, customer)] == ["stale", "stale", "pending"]

    run(scenario)


# Step 8: rate-limited flush ----------------------------------------------------------------


def test_step8_recovery_sends_nothing_and_the_flush_keeps_the_rate_cap(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customers = [chat(env, db, shop) for _ in range(5)]
        for customer in customers:
            await customer.send("menu")
        await outage(env, beat, timedelta(minutes=20))  # inside the staleness window
        await recovery(env, beat, shop).run()
        assert env.fake.sent == []
        sender = OutboxSender(env.db, env.providers, env.clock, rate_cap=RateCap(count=2))
        per_minute = []
        for _ in range(3):
            first = await sender.run_once(shop.tenant)
            again = await sender.run_once(shop.tenant)  # same minute: over the cap
            per_minute.append(first.sent + again.sent)
            env.clock.advance(timedelta(minutes=1))
        assert per_minute == [2, 2, 1]
        assert {m.to for m in env.fake.sent} == {c.phone for c in customers}

    run(scenario)


# Step 9: orders placed during recovery -----------------------------------------------------


def test_step9_orders_placed_from_the_backlog_are_highlighted_until_it_clears(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        held, later = chat(env, db, shop), chat(env, db, shop)
        start = await outage(env, beat, HOUR)
        # During the outage the ingress still stores messages: a whole order, unanswered.
        for text in ("menu", "1", "2", "checkout", "1", "House 1, Street 2", "skip", "1", "YES"):
            await held.deliver(text)
            env.clock.advance(timedelta(seconds=20))
        start_report = await recovery(env, beat, shop).run()
        (result,) = start_report.tenants
        assert result.backlog
        tracker = recovery(env, beat, shop).tracker(start_report)
        assert await held.processor().run_once(shop.tenant) == 9
        await tracker.inbox_processed(shop.tenant)
        assert recovery_row(db, shop, start)["backlog_cleared_at"] == env.clock.now()
        assert tracker.open_runs == set()
        env.clock.advance(timedelta(minutes=1))
        await place(later)
        orders = await OrderService(env.db, env.clock).list_orders(manager(db, shop))
        flags = {o.id: o.placed_during_recovery for o in orders}
        ((held_order, *_),) = orders_of(db, held)
        ((later_order, *_),) = orders_of(db, later)
        assert flags[held_order] is True
        assert flags[later_order] is False

    run(scenario)


# Exactly once ------------------------------------------------------------------------------


def test_an_interrupted_recovery_resumes_without_extending_a_window_twice(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)
        customer = chat(env, db, shop)
        await place(customer, payment="transfer")
        (_, _, _, _, due) = orders_of(db, customer)[0]
        start = await outage(env, beat, HOUR)

        class Breaks(ScopedDispatcher):
            async def tenants_for_recovery(self) -> list[TenantId]:
                # A tenant that fails (it doesn't exist), after ours.
                return [shop.tenant, TenantId(uuid.uuid4())]

        broken = Recovery(env.db, env.pool, Breaks(env.dispatcher, shop), env.clock, heartbeat=beat)
        with pytest.raises(RecoveryError):
            await broken.run()
        extended = orders_of(db, customer)[0][4]
        assert extended > due
        # The heartbeat still says when the outage began, so the restart finds it again.
        env.clock.advance(timedelta(minutes=5))
        report = await recovery(env, beat, shop).run()
        assert report.outage_started_at == start
        assert report.already_done == [shop.tenant] and report.tenants == []
        assert orders_of(db, customer)[0][4] == extended
        # And directly: the same outage twice does nothing the second time.
        async with env.db.tenant(shop.tenant) as conn:
            again = await recover_tenant(
                conn, shop.tenant, outage_started_at=start, now=env.clock.now()
            )
        assert again is None

    run(scenario)


# P11-T5: the recovery-mode drill -----------------------------------------------------------


def test_recovery_drill_after_a_one_hour_outage(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        beat = heartbeat(env)

        def worker_for(start: Recovery, report: Any) -> Worker:
            engine = chat(env, db, shop).engine
            return Worker(
                ScopedDispatcher(env.dispatcher, shop),
                InboxProcessor(
                    env.db,
                    env.clock,
                    {"message": engine.handle, "delivery_status": apply_delivery_status},
                ),
                OutboxSender(env.db, env.providers, env.clock),
                ChannelMonitor(env.db, env.providers, env.clock),
                env.clock,
                timers=OrderTimers(env.db, env.clock),
                recovery=start.tracker(report),
            )

        async def say(worker: Worker, customer: Customer, *texts: str) -> None:
            for text in texts:
                await customer.deliver(text)
                await worker.run_round()
                env.clock.advance(timedelta(seconds=5))

        checkout = ("menu", "1", "2", "checkout", "1", "House 1, Street 2", "skip")
        # A normal start, then normal trade.
        first = recovery(env, beat, shop)
        worker = worker_for(first, await first.run())
        waiting, paying, in_flight, stale_yes, held = (chat(env, db, shop) for _ in range(5))
        await say(worker, waiting, *checkout, "1")  # a cash summary, never confirmed
        await say(worker, paying, *checkout, "2", "YES")  # a transfer order, unpaid
        await say(worker, stale_yes, *checkout, "1")  # a summary it will confirm too late
        await in_flight.deliver("menu")
        await in_flight.processor().run_once(shop.tenant)
        with db.admin() as conn:  # the crash: this reply was being sent
            conn.execute(
                "UPDATE velora.outbox_messages SET status = 'sending', "
                "claim_token = gen_random_uuid(), locked_until = %s "
                "WHERE tenant_id = %s AND ordering_key = %s",
                (env.clock.now() + timedelta(minutes=2), shop.tenant.value, in_flight.phone),
            )
        ((paying_order, _, _, _, due),) = orders_of(db, paying)

        # The outage: one hour. The worker is down; the ingress still stores messages.
        env.clock.advance(HOUR)
        await stale_yes.deliver("YES")
        for text in (*checkout, "1", "YES"):
            await held.deliver(text)
            env.clock.advance(timedelta(seconds=20))

        # The restart.
        second = recovery(env, beat, shop)
        report = await second.run()
        assert report.outage is not None
        assert HOUR - HEARTBEAT_INTERVAL < report.outage <= HOUR + timedelta(minutes=4)
        worker = worker_for(second, report)
        for _ in range(40):
            await worker.run_round()
            env.clock.advance(timedelta(seconds=5))

        # No duplicate orders, and no stale YES accepted.
        assert orders_of(db, waiting) == []
        assert orders_of(db, stale_yes) == []
        assert copy.RECONFIRM_AFTER_OUTAGE in stale_yes.all_replies()
        assert any(r.startswith(copy.SUMMARY_UPDATED) for r in stale_yes.all_replies())
        assert len(orders_of(db, paying)) == 1
        assert len(orders_of(db, held)) == 1
        # The transfer window was extended by the outage, so the order didn't expire.
        ((_, status, _, _, new_due),) = orders_of(db, paying)
        assert status == "awaiting_transfer"
        assert new_due == due + report.outage
        # Every outbox message resolved: none pending, held or mid-send.
        statuses = rows(
            db,
            "SELECT status, count(*) FROM velora.outbox_messages WHERE tenant_id = %s "
            "AND created_at >= %s GROUP BY 1",
            shop.tenant.value,
            report.started_at - 2 * HOUR,
        )
        assert {s for s, _ in statuses} <= {"sent", "unknown", "stale"}
        # The message in flight at the crash was not sent again.
        assert env.fake.texts_to(in_flight.phone) == []
        assert [s for s, _ in outbox_of(db, in_flight)] == ["unknown"]
        # Every inbound message was processed, and the recovery window closed.
        assert rows(
            db,
            "SELECT DISTINCT status FROM velora.inbox_events WHERE tenant_id = %s "
            "AND event_type = 'message'",
            shop.tenant.value,
        ) == [("done",)]
        run_row = recovery_row(db, shop, report.outage_started_at)
        assert run_row["backlog_cleared_at"] is not None
        # The order placed from the held messages is highlighted for staff; the other not.
        orders = await OrderService(env.db, env.clock).list_orders(manager(db, shop))
        flags = {o.id: o.placed_during_recovery for o in orders}
        assert flags[orders_of(db, held)[0][0]] is True
        assert flags[paying_order] is False
        # The flush kept the rate cap (20 per channel per minute).
        per_minute = rows(
            db,
            "SELECT date_trunc('minute', sent_at), count(*) FROM velora.outbox_messages "
            "WHERE tenant_id = %s AND sent_at IS NOT NULL GROUP BY 1",
            shop.tenant.value,
        )
        assert max(n for _, n in per_minute) <= 20

    run(scenario)

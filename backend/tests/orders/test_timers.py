"""P7-T4: timers, on a simulated clock.

- An unpaid transfer order becomes `expired` once its window has passed, and the customer is
  told; it is not touched before.
- The worker finds the tenant when a window is due and expires the order in its round.
- Nonce expiry needs no timer; `test_confirmation.py` covers it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env, ScopedDispatcher
from tests.orders.conftest import Shop, chat, orders_of, rows, to_summary
from velora.messaging.channels import ChannelMonitor
from velora.messaging.inbox import InboxProcessor
from velora.messaging.outbox import OutboxSender
from velora.messaging.providers.fake import FakeProvider
from velora.orders.timers import OrderTimers
from velora.workers.runner import Worker

WINDOW = timedelta(minutes=30)


def test_an_unpaid_transfer_order_expires_after_its_window(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, payment="transfer")
        await customer.send("YES")
        ((order_id, status, _, _, due),) = orders_of(db, customer)
        assert status == "awaiting_transfer"
        timers = OrderTimers(env.db, env.clock)
        env.clock.advance(due - env.clock.now() - timedelta(seconds=1))
        assert (await timers.run_once(shop.tenant)).expired == 0
        assert orders_of(db, customer)[0][1] == "awaiting_transfer"
        env.clock.advance(timedelta(seconds=1))
        assert (await timers.run_once(shop.tenant)).expired == 1
        assert orders_of(db, customer)[0][1] == "expired"
        (notice,) = customer.new_replies()
        assert notice.startswith("We didn't receive payment for order #LS-")
        assert rows(
            db,
            "SELECT from_status, actor_kind, reason FROM velora.order_transitions "
            "WHERE order_id = %s AND to_status = 'expired'",
            order_id,
        ) == [("awaiting_transfer", "system", "transfer window passed")]
        # Running again changes nothing.
        assert (await timers.run_once(shop.tenant)).expired == 0

    run(scenario)


def test_a_cash_order_never_expires(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")
        env.clock.advance(timedelta(days=1))
        assert (await OrderTimers(env.db, env.clock).run_once(shop.tenant)).expired == 0
        assert orders_of(db, customer)[0][1] == "cod_confirmed"

    run(scenario)


def test_the_worker_expires_due_orders_and_tells_the_customer(
    run: Any, db: PgDatabase, shop: Shop, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, payment="transfer")
        await customer.send("YES")
        dispatcher = ScopedDispatcher(env.dispatcher, shop)
        worker = Worker(
            dispatcher,
            InboxProcessor(env.db, env.clock, {}),
            OutboxSender(env.db, env.providers, env.clock),
            ChannelMonitor(env.db, env.providers, env.clock),
            env.clock,
            timers=OrderTimers(env.db, env.clock),
        )
        await worker.run_round()  # sends the queued replies; nothing is due yet
        assert orders_of(db, customer)[0][1] == "awaiting_transfer"
        assert await dispatcher.tenants_with_work(["message"]) == []
        env.clock.advance(WINDOW)
        assert await dispatcher.tenants_with_work(["message"]) == [shop.tenant]
        await worker.run_round()
        assert orders_of(db, customer)[0][1] == "expired"
        assert fake.texts_to(customer.phone)[-1].startswith("We didn't receive payment")

    run(scenario)

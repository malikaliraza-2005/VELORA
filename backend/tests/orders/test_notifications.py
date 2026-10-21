"""P7-T6: customer status messages through the outbox.

Exit criterion: **order state never depends on whether a message was delivered.** With every
send failing, each transition still succeeds and the order ends in the right state.
"""

from __future__ import annotations

from typing import Any

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from tests.orders.conftest import Shop, chat, orders_of, rows, staff_id_of, to_summary
from velora.messaging.outbox import OutboxSender
from velora.messaging.provider import PermanentSendError
from velora.messaging.providers.fake import FakeProvider
from velora.orders.lifecycle import Actor, Status, transition

PATH: list[Status] = ["accepted", "preparing", "out_for_delivery", "completed"]


def test_transitions_succeed_while_every_send_fails(
    run: Any, db: PgDatabase, shop: Shop, fake: FakeProvider
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")
        ((order_id, *_),) = orders_of(db, customer)
        staff = Actor("staff", staff_id_of(db, shop))
        sender = OutboxSender(env.db, env.providers, env.clock)
        fake.fail_next(shop.session_ref, *(PermanentSendError("rejected") for _ in range(50)))
        for to in PATH:
            async with env.db.tenant(shop.tenant) as conn:
                await transition(
                    conn, shop.tenant, order_id, to=to, actor=staff, now=env.clock.now()
                )
            await sender.run_once(shop.tenant)
            assert orders_of(db, customer)[0][1] == to
        updates = rows(
            db,
            "SELECT status, payload->>'text' FROM velora.outbox_messages "
            "WHERE conversation_id IS NOT NULL AND ordering_key = %s "
            "AND purpose = 'status_update' ORDER BY seq",
            customer.phone,
        )
        # The confirmation plus accepted, out for delivery and completed ("preparing" has no
        # message); none was delivered.
        assert [status for status, _ in updates] == ["failed"] * 4
        assert updates[1][1].endswith("accepted — being prepared.")
        assert fake.texts_to(customer.phone) == []

    run(scenario)


def test_opted_out_customers_get_no_status_messages(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")
        await customer.send("STOP")
        ((order_id, *_),) = orders_of(db, customer)
        async with env.db.tenant(shop.tenant) as conn:
            await transition(
                conn,
                shop.tenant,
                order_id,
                to="accepted",
                actor=Actor("staff", staff_id_of(db, shop)),
                now=env.clock.now(),
            )
        assert customer.new_replies() == []
        assert orders_of(db, customer)[0][1] == "accepted"

    run(scenario)

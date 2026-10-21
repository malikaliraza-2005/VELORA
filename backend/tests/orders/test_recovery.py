"""P7-T7: the recovery hook.

Exit criterion: **pending actions created before an outage are expired during recovery
mode.** The customer is asked to confirm again, and the old YES can't place an order.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from tests.orders.conftest import Shop, chat, ids_of, orders_of, rows, to_summary
from velora.conversation import copy
from velora.orders.recovery import expire_pre_outage


def test_pending_actions_created_before_an_outage_are_expired_in_recovery(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        before, after = chat(env, db, shop), chat(env, db, shop)
        await to_summary(before)
        outage_started_at = env.clock.now()
        env.clock.advance(timedelta(seconds=1))
        await to_summary(after)  # issued after the outage began: kept
        voided = await expire_pre_outage(
            env.db, shop.tenant, outage_started_at=outage_started_at, now=env.clock.now()
        )
        assert voided == 1
        before_conversation, _ = ids_of(db, before)
        assert rows(
            db,
            "SELECT void_reason FROM velora.pending_actions WHERE conversation_id = %s",
            before_conversation,
        ) == [("recovery",)]
        assert before.new_replies() == [copy.RECONFIRM_AFTER_OUTAGE]
        # The old summary's YES places nothing; the customer gets a new summary instead.
        (again,) = await before.send("YES")
        assert again.startswith(copy.SUMMARY_UPDATED)
        assert orders_of(db, before) == []
        # The chat whose summary came after the outage began is untouched.
        assert after.new_replies() == []
        (placed,) = await after.send("YES")
        assert re.match(r"✅ Order #LS-\d{4} placed\.", placed)

    run(scenario)


def test_an_already_expired_summary_is_voided_without_a_message(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        env.clock.advance(timedelta(hours=1))
        outage_started_at = env.clock.now()
        env.clock.advance(timedelta(minutes=30))
        voided = await expire_pre_outage(
            env.db, shop.tenant, outage_started_at=outage_started_at, now=env.clock.now()
        )
        assert voided == 1
        assert customer.new_replies() == []

    run(scenario)

"""P7-T2 and T5: the order-creation transaction and the possible-duplicate flag.

Exit criteria covered here:
- **Double YES:** two YES replies at the same moment create exactly one order (100 runs).
- **Stale confirmation:** a price change or an item becoming unavailable between the summary
  and YES causes rejection and re-confirmation.
- An injected failure at each step commits nothing.
- A second identical order within 10 minutes is flagged for staff and not cancelled.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest

from tests.conversation.conftest import MENU, publish_menu, set_available, set_settings
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from tests.orders.conftest import (
    ADDRESS,
    BEEF,
    BEEF_COD_TOTAL,
    BEEF_TRANSFER_TOTAL,
    DELIVERY_FEE,
    TAX_COD_BPS,
    Customer,
    Shop,
    chat,
    ids_of,
    orders_of,
    rows,
    slot_count,
    summary_of,
    to_summary,
)
from velora.conversation import copy
from velora.menu.model import format_rupees
from velora.messaging.inbox import InboxEvent
from velora.ordering.pricing import tax_for
from velora.orders import placement
from velora.orders.placement import OrderRefused, place_order

DOUBLE_YES_RUNS = 100
DIRECT_RACE_RUNS = 20
PLACED = re.compile(r"✅ Order #LS-\d{4} placed\.\n")


def test_yes_creates_the_order_with_price_snapshots(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, coke=True)
        slots_before = slot_count(db, shop)
        (placed,) = await customer.send("YES")
        assert PLACED.match(placed)
        subtotal = BEEF + 12000
        total = subtotal + DELIVERY_FEE + tax_for(subtotal, TAX_COD_BPS)
        assert f"Total: {format_rupees(total)} (Cash on delivery)." in placed
        ((order_id, status, order_total, duplicate, due),) = orders_of(db, customer)
        assert (status, order_total, duplicate, due) == ("cod_confirmed", total, False, None)
        ((fee, tax_bps, address, menu_version, pending, placed_at, slot),) = rows(
            db,
            "SELECT delivery_fee_paisa, tax_bps, delivery_address, menu_version_id, "
            "pending_action_id, created_at, capacity_slot FROM velora.orders WHERE id = %s",
            order_id,
        )
        assert (fee, tax_bps, address) == (DELIVERY_FEE, TAX_COD_BPS, ADDRESS)
        assert menu_version is not None and slot is not None
        assert rows(
            db,
            "SELECT sku, name_snapshot, unit_price_paisa, quantity, line_total_paisa, choices "
            "FROM velora.order_items WHERE order_id = %s ORDER BY line_no",
            order_id,
        ) == [
            ("BEEF", "Beef Smash Burger", BEEF, 1, BEEF, []),
            ("COKE", "Coca-Cola 345ml", 12000, 1, 12000, []),
        ]
        assert rows(
            db,
            "SELECT from_status, to_status, actor_kind FROM velora.order_transitions "
            "WHERE order_id = %s",
            order_id,
        ) == [(None, "cod_confirmed", "customer")]
        assert rows(
            db,
            "SELECT action, actor_kind FROM velora.audit_log WHERE target_id = %s",
            order_id,
        ) == [("order.placed", "customer")]
        assert rows(
            db, "SELECT consumed_at FROM velora.pending_actions WHERE id = %s", pending
        ) == [(placed_at,)]
        # The confirmation is a critical status update, queued with the order.
        assert rows(
            db,
            "SELECT purpose, critical FROM velora.outbox_messages WHERE idempotency_key = %s",
            f"order-placed:{order_id}",
        ) == [("status_update", True)]
        assert slot_count(db, shop) == slots_before + 1
        state, context, _, _ = customer.conversation()
        assert (state, context) == ("idle", {})
        (status_reply,) = await customer.send("status")
        assert "placed (cash on delivery), waiting for the restaurant" in status_reply
        # A second YES has nothing to confirm.
        assert await customer.send("YES") == [copy.NOTHING_TO_CONFIRM]
        assert len(orders_of(db, customer)) == 1

    run(scenario)


def test_a_transfer_order_waits_for_payment(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, payment="transfer")
        placed_at = env.clock.now()
        (placed,) = await customer.send("YES")
        assert PLACED.match(placed)
        assert "Please pay by" in placed and "A screenshot alone does not confirm" in placed
        ((_, status, total, _, due),) = orders_of(db, customer)
        assert (status, total) == ("awaiting_transfer", BEEF_TRANSFER_TOTAL)
        assert due == placed_at + timedelta(minutes=30)  # the default transfer window

    run(scenario)


def test_a_takeaway_order_keeps_its_pickup_time(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, fulfilment="takeaway")
        _, checkout = summary_of(customer)
        (placed,) = await customer.send("YES")
        assert PLACED.match(placed)
        ((order_id, *_),) = orders_of(db, customer)
        assert rows(
            db,
            "SELECT fulfilment, pickup_at, delivery_fee_paisa, delivery_address "
            "FROM velora.orders WHERE id = %s",
            order_id,
        ) == [("takeaway", checkout.pickup_at, 0, None)]

    run(scenario)


def _yes_event(customer: Customer) -> InboxEvent:
    key = f"yes-{uuid.uuid4().hex}"
    payload = {
        "provider_message_id": key,
        "sender": customer.phone,
        "kind": "text",
        "text": "YES",
        "received_at": customer.env.clock.now().isoformat(),
        "media": None,
    }
    return InboxEvent(uuid.uuid4(), customer.shop.channel_id, "message", payload, 1, uuid.uuid4())


def test_two_simultaneous_yes_replies_create_exactly_one_order(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    """Two YES messages handled at the same moment by two transactions, bypassing the
    inbox's own ordering. 100 runs, each a new chat."""
    # Fewer checkout steps: delivery and cash only.
    set_settings(db, shop, takeaway_enabled=False, transfer_enabled=False)

    async def scenario(env: Env) -> None:
        for _ in range(DOUBLE_YES_RUNS):
            customer = chat(env, db, shop)
            for text in ("menu", "1", "2", "checkout", ADDRESS):
                await customer.send(text)
            (summary,) = await customer.send("skip")
            assert summary.startswith("🧾 Order summary")

            async def yes(c: Customer = customer) -> None:
                async with env.db.tenant(shop.tenant) as conn:
                    await c.engine.handle(conn, shop.tenant, _yes_event(c))

            await asyncio.gather(yes(), yes())
            replies = customer.new_replies()
            assert len(orders_of(db, customer)) == 1
            assert sum(bool(PLACED.match(r)) for r in replies) == 1
            assert copy.NOTHING_TO_CONFIRM in replies
            env.clock.advance(timedelta(seconds=5))

    run(scenario)


def test_racing_order_transactions_create_one_order_without_the_chat_lock(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    """The database guards alone: two transactions call `place_order` with the same nonce,
    without the engine's conversation lock."""

    async def scenario(env: Env) -> None:
        for _ in range(DIRECT_RACE_RUNS):
            customer = chat(env, db, shop)
            await to_summary(customer)
            nonce, checkout = summary_of(customer)
            conversation_id, customer_id = ids_of(db, customer)

            async def attempt(
                nonce: str = nonce,
                ids: tuple[uuid.UUID, uuid.UUID] = (conversation_id, customer_id),
                checkout: placement.Checkout = checkout,
            ) -> placement.Placed:
                async with env.db.tenant(shop.tenant) as conn:
                    return await place_order(
                        conn,
                        shop.tenant,
                        nonce,
                        conversation_id=ids[0],
                        customer_id=ids[1],
                        checkout=checkout,
                        now=env.clock.now(),
                    )

            results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
            placed = [r for r in results if isinstance(r, placement.Placed)]
            refused = [r for r in results if isinstance(r, OrderRefused)]
            assert (len(placed), len(refused)) == (1, 1), results
            assert len(orders_of(db, customer)) == 1

    run(scenario)


def test_a_price_change_between_summary_and_yes_asks_again(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        # The restaurant publishes a new menu: the Beef Smash Burger now costs Rs 950.
        new_price = 95000
        menu = [
            (category, [(s, n, new_price if s == "BEEF" else p, g) for s, n, p, g in items])
            for category, items in MENU
        ]
        publish_menu(db, shop, menu, version_no=3)
        (again,) = await customer.send("YES")
        assert again.startswith(copy.SUMMARY_UPDATED)
        assert f"1 × Beef Smash Burger — {format_rupees(new_price)}" in again
        assert orders_of(db, customer) == []
        (placed,) = await customer.send("YES")
        assert PLACED.match(placed)
        total = new_price + DELIVERY_FEE + tax_for(new_price, TAX_COD_BPS)
        ((_, _, order_total, _, _),) = orders_of(db, customer)
        assert order_total == total

    run(scenario)


def test_an_item_running_out_between_summary_and_yes_is_refused(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer, coke=True)
        set_available(db, shop, "COKE", False)
        (reply,) = await customer.send("YES")
        assert orders_of(db, customer) == []
        # Re-confirmation: the cart without the Coca-Cola, and CHECKOUT to go again.
        assert reply.startswith("🛒 Your cart:\n1. 1 × Beef Smash Burger")
        assert "Removed Coca-Cola 345ml: Coca-Cola 345ml isn't available right now." in reply
        assert "Reply CHECKOUT to order" in reply
        state, context, _, _ = customer.conversation()
        assert state == "browsing" and [line["sku"] for line in context["cart"]] == ["BEEF"]

    run(scenario)


def test_a_pause_between_summary_and_yes_refuses_the_order(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        set_settings(db, shop, paused_until=env.clock.now() + timedelta(minutes=20))
        (closed,) = await customer.send("YES")
        assert "isn't taking new orders right now (paused for a while)" in closed
        assert orders_of(db, customer) == []

    run(scenario)


def test_a_full_capacity_slot_at_yes_refuses_the_order(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        set_settings(db, shop, max_orders_per_slot=max(1, slot_count(db, shop)))
        before = slot_count(db, shop)
        (closed,) = await customer.send("YES")
        assert "isn't taking new orders right now (very busy)" in closed
        assert orders_of(db, customer) == []
        assert slot_count(db, shop) == before

    run(scenario)


STEPS = ["revalidate", "reprice", "consume", "create", "announce"]


@pytest.mark.parametrize("step", STEPS)
def test_an_injected_failure_at_each_step_commits_nothing(
    run: Any, db: PgDatabase, shop: Shop, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """The step runs (and writes what it writes), then fails. Nothing of the order is kept,
    the customer is told, and the same YES works once the fault is gone."""
    original: Callable[..., Awaitable[Any]] = getattr(placement, step)

    async def failing(*args: Any, **kwargs: Any) -> Any:
        await original(*args, **kwargs)
        raise RuntimeError(f"injected failure after {step}")

    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        conversation_id, _ = ids_of(db, customer)
        slots, audit = slot_count(db, shop), _count(db, shop, "audit_log")
        outbox = _count(db, shop, "outbox_messages")
        monkeypatch.setattr(placement, step, failing)
        assert await customer.send("YES") == [copy.ORDER_FAILED]
        monkeypatch.setattr(placement, step, original)
        assert orders_of(db, customer) == []
        assert rows(
            db,
            "SELECT count(*) FROM velora.pending_actions WHERE conversation_id = %s "
            "AND consumed_at IS NOT NULL",
            conversation_id,
        ) == [(0,)]
        assert slot_count(db, shop) == slots
        assert _count(db, shop, "audit_log") == audit
        assert _count(db, shop, "outbox_messages") == outbox + 1  # the apology only
        assert customer.conversation()[0] == "summary"
        (placed,) = await customer.send("YES")
        assert PLACED.match(placed)
        assert len(orders_of(db, customer)) == 1

    run(scenario)


def _count(pg: PgDatabase, shop: Shop, table: str) -> int:
    found = rows(
        pg,
        f"SELECT count(*) FROM velora.{table} WHERE tenant_id = %s",  # noqa: S608 - fixed names
        shop.tenant.value,
    )
    return int(found[0][0])


def test_a_second_identical_order_within_ten_minutes_is_flagged_not_cancelled(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for _ in range(2):
            await to_summary(customer)
            (placed,) = await customer.send("YES")
            assert PLACED.match(placed)
        env.clock.advance(timedelta(minutes=10))
        await to_summary(customer)
        await customer.send("YES")
        found = orders_of(db, customer)
        assert [(status, total, flagged) for _, status, total, flagged, _ in found] == [
            ("cod_confirmed", BEEF_COD_TOTAL, False),
            ("cod_confirmed", BEEF_COD_TOTAL, True),  # flagged for staff, still placed
            ("cod_confirmed", BEEF_COD_TOTAL, False),  # more than 10 minutes later
        ]

    run(scenario)

"""P7-T1: the YES confirmation nonce.

Exit criterion: **an expired nonce can't create an order.** Also: a nonce bound to another
customer or another cart is rejected, a nonce works once, and a new summary voids the old one.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from tests.orders.conftest import (
    Shop,
    chat,
    ids_of,
    orders_of,
    rows,
    summary_of,
    to_summary,
)
from velora.conversation import copy
from velora.conversation.engine import SUMMARY_TTL
from velora.ordering.pricing import CartLine
from velora.orders.placement import OrderRefused, place_order


def test_an_expired_nonce_cannot_create_an_order(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        nonce, checkout = summary_of(customer)
        conversation_id, customer_id = ids_of(db, customer)
        env.clock.advance(SUMMARY_TTL)
        # The order transaction itself refuses it...
        async with env.db.tenant(shop.tenant) as conn:
            with pytest.raises(OrderRefused) as refused:
                await place_order(
                    conn,
                    shop.tenant,
                    nonce,
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    checkout=checkout,
                    now=env.clock.now(),
                )
        assert refused.value.code == "nonce"
        # ...and in the chat, YES shows a fresh summary to confirm instead.
        (again,) = await customer.send("YES")
        assert again.startswith(copy.SUMMARY_UPDATED)
        assert orders_of(db, customer) == []

    run(scenario)


def test_a_nonce_bound_to_another_customer_or_cart_is_rejected(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        alice, bob = chat(env, db, shop), chat(env, db, shop)
        await to_summary(alice)
        await to_summary(bob)
        nonce, checkout = summary_of(alice)
        alice_conversation, alice_id = ids_of(db, alice)
        bob_conversation, bob_id = ids_of(db, bob)
        other_cart = replace(checkout, lines=(CartLine("BEEF", 2),))
        attempts = [
            (bob_conversation, bob_id, checkout),  # Bob replays Alice's nonce
            (alice_conversation, bob_id, checkout),  # wrong customer
            (bob_conversation, alice_id, checkout),  # wrong conversation
            (alice_conversation, alice_id, other_cart),  # Alice, but another cart
            (alice_conversation, alice_id, replace(checkout, address="Somewhere else 123")),
        ]
        for conversation_id, customer_id, confirmed in attempts:
            async with env.db.tenant(shop.tenant) as conn:
                with pytest.raises(OrderRefused) as refused:
                    await place_order(
                        conn,
                        shop.tenant,
                        nonce,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        checkout=confirmed,
                        now=env.clock.now(),
                    )
            assert refused.value.code == "nonce"
        assert orders_of(db, alice) == [] and orders_of(db, bob) == []
        # The nonce itself was fine: bound correctly, it places Alice's order.
        async with env.db.tenant(shop.tenant) as conn:
            await place_order(
                conn,
                shop.tenant,
                nonce,
                conversation_id=alice_conversation,
                customer_id=alice_id,
                checkout=checkout,
                now=env.clock.now(),
            )
        assert len(orders_of(db, alice)) == 1

    run(scenario)


def test_a_nonce_works_once(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        nonce, checkout = summary_of(customer)
        conversation_id, customer_id = ids_of(db, customer)
        for attempt in range(2):
            async with env.db.tenant(shop.tenant) as conn:
                call = place_order(
                    conn,
                    shop.tenant,
                    nonce,
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    checkout=checkout,
                    now=env.clock.now(),
                )
                if attempt == 0:
                    await call
                else:
                    with pytest.raises(OrderRefused, match="expired|used"):
                        await call
        assert len(orders_of(db, customer)) == 1

    run(scenario)


def test_a_new_summary_voids_the_previous_nonce(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        old_nonce, checkout = summary_of(customer)
        # Check out again: a new summary, with a new nonce.
        for text in ("checkout", "1", "House 12, Street 4, DHA Phase 5", "skip", "1"):
            await customer.send(text)
        new_nonce, _ = summary_of(customer)
        assert new_nonce != old_nonce
        conversation_id, customer_id = ids_of(db, customer)
        found = rows(
            db,
            "SELECT void_reason FROM velora.pending_actions WHERE conversation_id = %s "
            "ORDER BY created_at",
            conversation_id,
        )
        assert [r[0] for r in found] == ["superseded", None]
        async with env.db.tenant(shop.tenant) as conn:
            with pytest.raises(OrderRefused):
                await place_order(
                    conn,
                    shop.tenant,
                    old_nonce,
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    checkout=checkout,
                    now=env.clock.now(),
                )
        assert orders_of(db, customer) == []

    run(scenario)


def test_the_nonce_is_stored_only_as_a_hash(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        nonce, _ = summary_of(customer)
        stored = rows(
            db,
            "SELECT nonce_hash, payload::text FROM velora.pending_actions WHERE tenant_id = %s "
            "AND voided_at IS NULL AND consumed_at IS NULL AND customer_id = %s",
            shop.tenant.value,
            ids_of(db, customer)[1],
        )
        assert len(stored) == 1
        nonce_hash, payload = stored[0]
        assert nonce.encode() not in bytes(nonce_hash) and nonce not in payload

    run(scenario)

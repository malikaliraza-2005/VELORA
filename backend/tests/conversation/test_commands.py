"""P6-T2: fixed commands and disclosures (AD-24, AD-14).

- The first reply discloses the automated assistant, how to reach staff (0) and STOP.
- Only exactly "STOP" opts out, and then nothing more is sent until START.
- "CANCEL" clears the cart and never opts out.
- "status" reports the customer's latest order; voice notes get a request to type.
"""

from __future__ import annotations

from typing import Any

from tests.conversation.conftest import Shop, chat, rows
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from velora.conversation import copy


def test_the_first_reply_contains_the_disclosure_and_later_ones_do_not(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        first = await customer.send("Assalam o alaikum, menu bhej dein")
        assert len(first) == 1
        assert "automated ordering assistant" in first[0]
        assert "Reply 0 anytime to talk to staff" in first[0]
        assert "STOP" in first[0]
        assert "1. Burgers" in first[0]
        later = await customer.send("menu")
        assert later and not any("automated ordering assistant" in r for r in later)

    run(scenario)


def test_cancel_never_opts_the_customer_out(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        await customer.send("2")
        await customer.send("1")  # Coca-Cola into the cart
        for text in ("CANCEL", "cancel", "Cancel"):
            assert await customer.send(text) == [copy.CART_CLEARED]
            state, context, _, opted_out_at = customer.conversation()
            assert opted_out_at is None
            assert "cart" not in context
            assert state == "idle"
        assert await customer.send("menu")  # still answered

    run(scenario)


def test_stop_opts_out_and_stops_further_messages(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        # Replies still queued (not yet sent) when STOP arrives are not sent either. No sender
        # runs here, so both earlier replies are still queued.
        await customer.send("hi")
        await customer.deliver("menu")
        await customer.deliver("STOP")
        await customer.processor().run_once(shop.tenant)
        assert customer.all_replies() == [copy.opted_out(f"Test {_slug(db, shop)}")]
        customer.new_replies()
        _, _, _, opted_out_at = customer.conversation()
        assert opted_out_at is not None
        cancelled = rows(
            db,
            "SELECT count(*) FROM velora.outbox_messages WHERE tenant_id = %s "
            "AND ordering_key = %s AND status = 'cancelled'",
            shop.tenant.value,
            customer.phone,
        )
        assert cancelled[0][0] == 2
        for text in ("menu", "hi", "0", "CANCEL", "STOP", "2 zinger"):
            assert await customer.send(text) == []
        # START resumes, with the disclosure again.
        back = await customer.send("START")
        assert back and "automated ordering assistant" in back[0]
        assert customer.conversation()[3] is None

    run(scenario)


def test_only_exactly_stop_opts_out(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("stop please", "don't stop", "STOP!", "stopp"):
            await customer.send(text)
            assert customer.conversation()[3] is None, text
        await customer.send("  stop  ")
        assert customer.conversation()[3] is not None

    run(scenario)


def test_status_reports_the_latest_order(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        newcomer = chat(env, db, shop)
        (reply,) = await newcomer.send("status")
        assert reply.endswith(copy.NO_ORDERS)
        # The seeded customer of this shop has one cash-on-delivery order.
        regular = chat(env, db, shop)
        regular.phone = "+923000000001"
        (reply,) = await regular.send("status")
        assert "Order #LS-" in reply.split("\n\n")[-1]
        assert "placed (cash on delivery)" in reply

    run(scenario)


def test_a_voice_note_gets_a_request_to_type(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("hi")
        assert await customer.send(None, kind="audio") == [copy.VOICE_NOTE]
        assert await customer.send(None, kind="image") == [copy.TEXT_ONLY]
        state, context, _, _ = customer.conversation()
        assert "misses" not in context  # media isn't counted as not understood
        assert state == "browsing"

    run(scenario)


def _slug(db: PgDatabase, shop: Shop) -> str:
    found = rows(db, "SELECT slug FROM velora.tenants WHERE id = %s", shop.tenant.value)
    return str(found[0][0])

"""P8-T2, T3, T5 from the customer's side, through a real chat (FakeProvider, inbox, engine).

Exit criteria covered here:
- **a screenshot upload alone never changes payment status;**
- **text such as "I have paid" never changes payment status** (typed, or read by the AI);
- **an expired transfer moves to `expired`, not paid.**

Also: the account details reach the customer, a first COD order over the limit is refused
with a clear message, and a blocklisted customer can't choose COD.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests.conversation.conftest import FakeProposer
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from tests.payments.conftest import (
    ADDRESS,
    BEEF_COD_TOTAL,
    TRANSFER_DETAILS,
    Customer,
    Shop,
    chat,
    evidence_of,
    orders_of,
    payment_state,
    rows,
    set_cod_block,
    set_settings,
    to_summary,
)
from velora.ai.proposer import Proposal
from velora.conversation import copy
from velora.menu.model import format_rupees
from velora.messaging.provider import InboundMedia
from velora.orders.timers import OrderTimers

SCREENSHOT = InboundMedia("media-screenshot-1", "image/jpeg")


async def transfer_order(customer: Customer) -> tuple[Any, str]:
    """Place a transfer order. Returns (order id, the confirmation)."""
    await to_summary(customer, payment="transfer")
    placed = "\n".join(await customer.send("YES"))
    ((order_id, status, *_),) = orders_of(customer.pg, customer)
    assert status == "awaiting_transfer"
    return order_id, placed


def reference_of(placed: str) -> str:
    return placed.split("Order #", 1)[1].split(" ", 1)[0]


# Transfer instructions (T3) ----------------------------------------------------------------


def test_a_transfer_order_carries_the_restaurants_account_details(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        _, placed = await transfer_order(customer)
        assert TRANSFER_DETAILS in placed
        assert "A screenshot alone does not confirm payment." in placed
        # "status" repeats where and by when to pay.
        status, reminder = await customer.send("status")
        assert "waiting for your transfer" in status
        assert TRANSFER_DETAILS in reminder

    run(scenario)


def test_transfer_is_not_offered_without_account_details(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        set_settings(db, shop, transfer_account_details=None)
        customer = chat(env, db, shop)
        for text in ("menu", "1", "2", "checkout", "1", ADDRESS):
            await customer.send(text)
        # Only cash remains, so the summary comes straight away.
        (summary,) = await customer.send("skip")
        assert summary.startswith("🧾 Order summary")
        assert "Cash on delivery" in summary

    run(scenario)


# Screenshots and claims never pay (T3) -------------------------------------------------------


def test_a_screenshot_is_kept_for_staff_and_changes_nothing(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        order_id, placed = await transfer_order(customer)
        before = payment_state(db, shop)
        (reply,) = await customer.send(None, "image", SCREENSHOT)
        assert reply == copy.evidence_received(reference_of(placed), expired=False)
        assert payment_state(db, shop) == before
        assert evidence_of(db, order_id) == [("image", "media-screenshot-1", "image/jpeg")]
        assert rows(
            db,
            "SELECT count(*) FROM velora.audit_log WHERE target_id = %s "
            "AND action = 'payment.evidence_received'",
            order_id,
        ) == [(1,)]

    run(scenario)


def test_i_have_paid_never_changes_payment_status(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        order_id, placed = await transfer_order(customer)
        before = payment_state(db, shop)
        for text in ("I have paid", "maine payment kar di hai", "PAID ✅", "transfer done"):
            (reply,) = await customer.send(text)
            assert reply == copy.evidence_received(reference_of(placed), expired=False), text
        assert payment_state(db, shop) == before
        assert [k for k, _, _ in evidence_of(db, order_id)] == ["text_claim"] * 4

    run(scenario)


def test_an_ai_payment_claim_never_changes_payment_status(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop, FakeProposer(Proposal("payment_claim")))
        order_id, _ = await transfer_order(customer)
        before = payment_state(db, shop)
        await customer.send("bhai paisay account mein chalay gaye hain check karo")
        assert payment_state(db, shop) == before
        assert [k for k, _, _ in evidence_of(db, order_id)] == ["text_claim"]

    run(scenario)


def test_without_a_transfer_order_a_screenshot_is_not_kept(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")  # a cash order
        count = "SELECT count(*) FROM velora.payment_evidence WHERE tenant_id = %s"
        before = rows(db, count, shop.tenant.value)
        (reply,) = await customer.send(None, "image", SCREENSHOT)
        assert reply == copy.TEXT_ONLY
        assert rows(db, count, shop.tenant.value) == before

    run(scenario)


def test_a_screenshot_during_handoff_is_kept_silently(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        order_id, _ = await transfer_order(customer)
        await customer.send("0")  # talk to staff
        assert await customer.send(None, "image", SCREENSHOT) == []
        assert evidence_of(db, order_id) == [("image", "media-screenshot-1", "image/jpeg")]
        assert orders_of(db, customer)[0][1] == "awaiting_transfer"

    run(scenario)


# Expiry and late evidence (T5) ---------------------------------------------------------------


def test_an_expired_transfer_is_expired_not_paid(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        order_id, placed = await transfer_order(customer)
        await customer.send(None, "image", SCREENSHOT)  # evidence before the window ends
        ((_, _, _, _, due),) = orders_of(db, customer)
        env.clock.advance(due - env.clock.now())
        assert (await OrderTimers(env.db, env.clock).run_once(shop.tenant)).expired == 1
        assert orders_of(db, customer)[0][1] == "expired"
        assert rows(db, "SELECT count(*) FROM velora.payments WHERE order_id = %s", order_id) == [
            (0,)
        ]
        customer.new_replies()
        # A late screenshot still reaches staff, who review it.
        (reply,) = await customer.send(None, "image", InboundMedia("media-late", "image/png"))
        assert reply == copy.evidence_received(reference_of(placed), expired=True)
        assert orders_of(db, customer)[0][1] == "expired"
        assert len(evidence_of(db, order_id)) == 2
        # After 24 hours it no longer attaches to the expired order.
        env.clock.advance(timedelta(hours=25))
        assert await customer.send(None, "image", SCREENSHOT) == [copy.TEXT_ONLY]

    run(scenario)


# Cash on delivery controls (T2) ------------------------------------------------------------


async def _to_payment_choice(customer: Customer) -> list[str]:
    for text in ("menu", "1", "2", "checkout", "1", ADDRESS):
        await customer.send(text)
    return await customer.send("skip")


def test_a_first_order_over_the_cod_limit_is_refused_with_a_clear_message(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    limit = BEEF_COD_TOTAL - 1

    async def scenario(env: Env) -> None:
        set_settings(db, shop, cod_first_order_limit_paisa=limit)
        customer = chat(env, db, shop)
        note, summary = await _to_payment_choice(customer)
        assert note == (
            f"Note: cash on delivery is available for first orders up to {format_rupees(limit)}."
        )
        assert "Bank / wallet transfer" in summary

        # The limit set after a cash summary was shown: YES is refused, with the reason.
        other = chat(env, db, shop)
        set_settings(db, shop, cod_first_order_limit_paisa=None)
        await to_summary(other)
        set_settings(db, shop, cod_first_order_limit_paisa=limit)
        (refusal,) = await other.send("YES")
        assert refusal.startswith("Sorry, cash on delivery isn't available for this order.")
        assert orders_of(db, other) == []

    run(scenario)


def test_a_customer_is_on_their_first_order_until_one_is_completed(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await to_summary(customer)
        await customer.send("YES")
        await customer.send("CANCEL")  # cancelled: still no completed order
        set_settings(db, shop, cod_first_order_limit_paisa=BEEF_COD_TOTAL - 1)
        note, _ = await _to_payment_choice(customer)
        assert note.startswith("Note: cash on delivery is available for first orders")
        await customer.send("CANCEL")

        set_settings(db, shop, cod_first_order_limit_paisa=None)
        await to_summary(customer)
        await customer.send("YES")
        complete_an_order(db, customer)
        set_settings(db, shop, cod_first_order_limit_paisa=BEEF_COD_TOTAL - 1)
        (choice,) = await _to_payment_choice(customer)
        assert choice.startswith("How will you pay?")

    run(scenario)


def complete_an_order(pg: PgDatabase, customer: Customer) -> None:
    """Take the customer's latest cash order through to `completed`, as the administrator."""
    with pg.admin() as conn:
        (order_id,) = conn.execute(
            "SELECT o.id FROM velora.orders o JOIN velora.customers cu ON cu.id = o.customer_id "
            "WHERE cu.phone_e164 = %s AND o.tenant_id = %s AND o.status = 'cod_confirmed' "
            "ORDER BY o.created_at DESC LIMIT 1",
            (customer.phone, customer.shop.tenant.value),
        ).fetchone() or (None,)
        assert order_id is not None
        for status in ("accepted", "preparing", "out_for_delivery", "completed"):
            conn.execute("UPDATE velora.orders SET status = %s WHERE id = %s", (status, order_id))


def test_a_blocklisted_customer_cannot_choose_cod(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await customer.send("menu")
        set_cod_block(db, customer, "two fake orders")
        note, summary = await _to_payment_choice(customer)
        assert note == f"Note: {copy.COD_BLOCKED}."
        assert "Bank / wallet transfer" in summary and "Cash on delivery" not in summary

        # Blocked after a cash summary was shown: YES is refused.
        other = chat(env, db, shop)
        await to_summary(other)
        set_cod_block(db, other, "fake order")
        (refusal,) = await other.send("YES")
        assert refusal.startswith(f"Sorry, {copy.COD_BLOCKED}.")
        assert orders_of(db, other) == []

        # Unblocked: cash is offered again.
        set_cod_block(db, customer, None)
        await customer.send("CANCEL")
        (choice,) = await _to_payment_choice(customer)
        assert choice.startswith("How will you pay?")

    run(scenario)

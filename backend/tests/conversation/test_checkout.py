"""P6-T5: cart validation, fulfilment details and the templated summary (AD-20).

Exit criteria covered here:
- **Prices always equal the database values**: every line, fee, tax and total shown is what
  the menu and settings say, computed independently in the test.
- **The tax line changes with the payment method.**
- **The AI output is never sent verbatim as a total** (also in test_ai.py).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests.conversation.conftest import (
    DELIVERY_FEE,
    DELIVERY_MIN,
    LARGE,
    PRICES,
    TAX_COD_BPS,
    TAX_TRANSFER_BPS,
    Customer,
    Shop,
    chat,
    set_settings,
)
from tests.db.conftest import PgDatabase
from tests.messaging.conftest import Env
from velora.conversation import copy
from velora.menu.model import format_rupees
from velora.ordering.pricing import tax_for


async def _two_large_zingers_and_a_coke(customer: Customer) -> None:
    await customer.send("menu")
    await customer.send("1")  # Burgers
    assert await customer.send("1 x2") == [
        "Size for Zinger Burger?\n1. Regular\n2. Large (+Rs 150)"
    ]
    (added,) = await customer.send("2")
    assert added.startswith(
        f"Added: 2 × Zinger Burger (Large) — {format_rupees(2 * (PRICES['ZINGER'] + LARGE))}"
    )
    await customer.send("menu")
    await customer.send("2")  # Drinks
    await customer.send("1")  # Coca-Cola


SUBTOTAL = 2 * (PRICES["ZINGER"] + LARGE) + PRICES["COKE"]


def _expected_summary_amounts(bps: int) -> tuple[int, int]:
    tax = tax_for(SUBTOTAL, bps)
    return tax, SUBTOTAL + DELIVERY_FEE + tax


def test_a_delivery_checkout_shows_database_prices_in_a_templated_summary(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await _two_large_zingers_and_a_coke(customer)
        (cart,) = await customer.send("cart")
        assert f"Subtotal: {format_rupees(SUBTOTAL)}" in cart
        (ask,) = await customer.send("checkout")
        assert ask.startswith("Delivery or takeaway?\n1. Delivery (Rs 150 delivery fee")
        assert await customer.send("1") == [copy.ADDRESS_ASK]
        assert await customer.send("short") == [copy.ADDRESS_TOO_SHORT]
        assert await customer.send("House 12, Street 4, DHA Phase 5") == [copy.NOTE_ASK]
        (pay,) = await customer.send("gate no. 2")
        assert pay == "How will you pay?\n1. Cash on delivery\n2. Bank / wallet transfer"
        (summary,) = await customer.send("1")
        tax, total = _expected_summary_amounts(TAX_COD_BPS)
        assert summary.split("\n") == [
            "🧾 Order summary — please check",
            f"2 × Zinger Burger (Large) — {format_rupees(2 * (PRICES['ZINGER'] + LARGE))}",
            f"1 × Coca-Cola 345ml — {format_rupees(PRICES['COKE'])}",
            f"Delivery — {format_rupees(DELIVERY_FEE)}",
            f"Tax (cash) 16% — {format_rupees(tax)}",
            "─────────────",
            f"TOTAL — {format_rupees(total)}",
            "Payment: Cash on delivery",
            "Deliver to: House 12, Street 4, DHA Phase 5",
            "Note: gate no. 2",
            "",
            copy.SUMMARY_FOOTER,
        ]
        state, context, _, _ = customer.conversation()
        assert state == "summary"
        assert context["quote"]["total_paisa"] == total
        # P7 creates the order; until then YES is answered honestly.
        assert await customer.send("YES") == [copy.NOT_LIVE_YET]

    run(scenario)


def test_the_tax_line_changes_with_the_payment_method(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        summaries = {}
        for choice, bps in (("1", TAX_COD_BPS), ("2", TAX_TRANSFER_BPS)):
            customer = chat(env, db, shop)
            await _two_large_zingers_and_a_coke(customer)
            await customer.send("checkout")
            await customer.send("delivery")
            await customer.send("House 12, Street 4, DHA Phase 5")
            await customer.send("skip")
            (summary,) = await customer.send(choice)
            tax, total = _expected_summary_amounts(bps)
            assert f"— {format_rupees(tax)}" in summary
            assert f"TOTAL — {format_rupees(total)}" in summary
            summaries[choice] = summary
        assert "Tax (cash) 16%" in summaries["1"]
        assert "Tax (digital payment) 5%" in summaries["2"]
        assert summaries["1"] != summaries["2"]

    run(scenario)


def test_an_expired_or_changed_summary_is_shown_again(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        await _two_large_zingers_and_a_coke(customer)
        for text in ("checkout", "1", "House 12, Street 4, DHA Phase 5", "skip", "2"):
            await customer.send(text)
        env.clock.advance(timedelta(minutes=11))
        (again,) = await customer.send("yes")
        assert again.startswith(copy.SUMMARY_UPDATED)
        # The fee changed after the summary: YES shows the new total instead of confirming.
        set_settings(db, shop, delivery_fee_paisa=DELIVERY_FEE + 5000)
        (changed,) = await customer.send("YES")
        assert changed.startswith(copy.SUMMARY_UPDATED)
        assert f"Delivery — {format_rupees(DELIVERY_FEE + 5000)}" in changed
        assert await customer.send("YES") == [copy.NOT_LIVE_YET]

    run(scenario)


def test_delivery_below_the_minimum_is_refused(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("menu", "2", "1"):  # one Coca-Cola
            await customer.send(text)
        await customer.send("checkout")
        (refused,) = await customer.send("1")
        assert refused.startswith(
            f"The minimum order for delivery is {format_rupees(DELIVERY_MIN)}"
        )
        assert customer.conversation()[0] == "browsing"

    run(scenario)


def test_takeaway_needs_a_pickup_time_inside_the_window(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        customer = chat(env, db, shop)
        for text in ("menu", "2", "1 x3"):
            await customer.send(text)
        await customer.send("checkout")
        (ask,) = await customer.send("2")
        assert ask.startswith(copy.PICKUP_ASK)
        assert "1. As soon as possible" in ask
        (late,) = await customer.send("in 3 days")
        assert late == copy.CHOOSE_NUMBER
        (early,) = await customer.send("5 min")
        assert early.startswith("Sorry, pickup must be between 20 and")
        await customer.send("1")
        (summary,) = await customer.send("cash")
        assert "Takeaway, pickup at" in summary
        assert "Delivery —" not in summary
        tax = tax_for(3 * PRICES["COKE"], TAX_COD_BPS)
        assert f"TOTAL — {format_rupees(3 * PRICES['COKE'] + tax)}" in summary

    run(scenario)


def test_a_paused_restaurant_starts_no_cart(run: Any, db: PgDatabase, shop: Shop) -> None:
    async def scenario(env: Env) -> None:
        set_settings(db, shop, paused_until=env.clock.now() + timedelta(minutes=20))
        customer = chat(env, db, shop)
        for text in ("menu", "2"):
            await customer.send(text)
        (closed,) = await customer.send("1")
        assert "isn't taking new orders right now (paused for a while)" in closed
        assert "cart" not in customer.conversation()[1]

    run(scenario)


def test_an_unset_tax_rate_removes_that_payment_method(
    run: Any, db: PgDatabase, shop: Shop
) -> None:
    async def scenario(env: Env) -> None:
        set_settings(db, shop, tax_transfer_bps=None)
        customer = chat(env, db, shop)
        await _two_large_zingers_and_a_coke(customer)
        for text in ("checkout", "1", "House 12, Street 4, DHA Phase 5"):
            await customer.send(text)
        (summary,) = await customer.send("skip")
        # Only cash on delivery can be quoted (B3), so it is chosen without asking.
        assert "Payment: Cash on delivery" in summary

    run(scenario)
